"""File integrity checker for incremental ingestion.

This module provides SHA256-based file integrity tracking to enable incremental
ingestion. Files that have been successfully processed can be skipped on
subsequent ingestion runs.

Design Principles:
- Idempotent: Multiple ingestion runs of the same file are safe
- Persistent: SQLite-backed storage survives process restarts
- Concurrent: WAL mode enables concurrent read/write operations
- Graceful: Failed ingestions are tracked but don't block retries
"""

import hashlib
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class FileIntegrityChecker(ABC):
    """Abstract base class for file integrity checking.
    
    Implementations track which files have been successfully processed
    to enable incremental ingestion.
    """
    
    @abstractmethod
    def compute_sha256(self, file_path: str) -> str:
        """Compute SHA256 hash of file.
        
        Args:
            file_path: Path to the file to hash.
            
        Returns:
            Hexadecimal SHA256 hash string (64 characters).
            
        Raises:
            FileNotFoundError: If file does not exist.
            IOError: If path is not a file or cannot be read.
        """
        pass
    
    @abstractmethod
    def should_skip(self, file_hash: str, collection: Optional[str] = None) -> bool:
        """Check if file should be skipped based on hash.
        
        Args:
            file_hash: SHA256 hash of the file.
            
        Returns:
            True if file has been successfully processed before, False otherwise.
        """
        pass
    
    @abstractmethod
    def mark_success(
        self, 
        file_hash: str, 
        file_path: str, 
        collection: Optional[str] = None
    ) -> None:
        """Mark file as successfully processed.
        
        Args:
            file_hash: SHA256 hash of the file.
            file_path: Original file path (for tracking).
            collection: Optional collection/namespace identifier.
            
        Raises:
            RuntimeError: If database operation fails.
        """
        pass
    
    @abstractmethod
    def mark_failed(
        self, 
        file_hash: str, 
        file_path: str, 
        error_msg: str,
        collection: Optional[str] = None
    ) -> None:
        """Mark file processing as failed.
        
        Failed files are tracked but not skipped on subsequent runs,
        allowing retries.
        
        Args:
            file_hash: SHA256 hash of the file.
            file_path: Original file path (for tracking).
            error_msg: Error message describing the failure.
            
        Raises:
            RuntimeError: If database operation fails.
        """
        pass

    @abstractmethod
    def remove_record(self, file_hash: str, collection: Optional[str] = None) -> bool:
        """Remove an ingestion record by its file hash.

        Args:
            file_hash: SHA256 hash identifying the record.

        Returns:
            True if a record was deleted, False if not found.
        """
        pass

    @abstractmethod
    def list_processed(
        self, collection: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List successfully processed files.

        Args:
            collection: Optional collection filter.  When *None* all
                successful records are returned.

        Returns:
            List of dicts with keys: file_hash, file_path, collection,
            processed_at, updated_at.
        """
        pass


class SQLiteIntegrityChecker(FileIntegrityChecker):
    """Track successful ingestion by both content hash and collection."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = None
        self._ensure_database()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __del__(self):
        self.close()

    def _ensure_database(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        db_file = Path(self.db_path)
        if db_file.exists() and db_file.stat().st_size:
            with db_file.open("rb") as stream:
                if stream.read(16) != b"SQLite format 3\x00":
                    raise sqlite3.DatabaseError(f"Invalid SQLite database header: {self.db_path}")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            columns = conn.execute("PRAGMA table_info(ingestion_history)").fetchall()
            if columns and not any(row[1] == "collection_key" for row in columns):
                # Migrate the previous global file_hash primary key without losing history.
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""CREATE TABLE ingestion_history_v2 (
                    file_hash TEXT NOT NULL, collection_key TEXT NOT NULL,
                    file_path TEXT NOT NULL, status TEXT NOT NULL, collection TEXT,
                    error_msg TEXT, processed_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (file_hash, collection_key))""")
                conn.execute("""INSERT INTO ingestion_history_v2
                    SELECT file_hash, COALESCE(collection, ''), file_path, status,
                           collection, error_msg, processed_at, updated_at
                    FROM ingestion_history""")
                conn.execute("DROP TABLE ingestion_history")
                conn.execute("ALTER TABLE ingestion_history_v2 RENAME TO ingestion_history")
            conn.execute("""CREATE TABLE IF NOT EXISTS ingestion_history (
                file_hash TEXT NOT NULL, collection_key TEXT NOT NULL,
                file_path TEXT NOT NULL, status TEXT NOT NULL, collection TEXT,
                error_msg TEXT, processed_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (file_hash, collection_key))""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON ingestion_history(status)")

    def compute_sha256(self, file_path: str) -> str:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if not path.is_file():
            raise IOError(f"Path is not a file: {file_path}")
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(65536), b""):
                    digest.update(block)
        except OSError as exc:
            raise IOError(f"Failed to read file {file_path}: {exc}") from exc
        return digest.hexdigest()

    @staticmethod
    def _key(collection: Optional[str]) -> str:
        return collection or ""

    def should_skip(self, file_hash: str, collection: Optional[str] = None) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status FROM ingestion_history WHERE file_hash=? AND collection_key=?",
                (file_hash, self._key(collection)),
            ).fetchone()
            return row is not None and row[0] == "success"

    def _mark(self, file_hash: str, file_path: str, collection: Optional[str],
              status: str, error_msg: Optional[str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""INSERT INTO ingestion_history
                    (file_hash, collection_key, file_path, status, collection,
                     error_msg, processed_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_hash, collection_key) DO UPDATE SET
                      file_path=excluded.file_path, status=excluded.status,
                      error_msg=excluded.error_msg, updated_at=excluded.updated_at""",
                    (file_hash, self._key(collection), file_path, status,
                     collection, error_msg, now, now))
        except sqlite3.Error as exc:
            raise RuntimeError(f"Failed to mark {status} for {file_path}: {exc}") from exc

    def mark_success(self, file_hash: str, file_path: str,
                     collection: Optional[str] = None) -> None:
        self._mark(file_hash, file_path, collection, "success", None)

    def mark_failed(self, file_hash: str, file_path: str, error_msg: str,
                    collection: Optional[str] = None) -> None:
        self._mark(file_hash, file_path, collection, "failed", error_msg)

    def remove_record(self, file_hash: str, collection: Optional[str] = None) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM ingestion_history WHERE file_hash=? AND collection_key=?",
                    (file_hash, self._key(collection)),
                )
                return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise RuntimeError(f"Failed to remove record {file_hash}: {exc}") from exc

    def list_processed(self, collection: Optional[str] = None) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            sql = """SELECT file_hash, file_path, collection, processed_at, updated_at
                     FROM ingestion_history WHERE status='success'"""
            params: tuple[str, ...] = ()
            if collection is not None:
                sql += " AND collection_key=?"
                params = (self._key(collection),)
            sql += " ORDER BY processed_at ASC"
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
