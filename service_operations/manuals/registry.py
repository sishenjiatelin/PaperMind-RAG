"""Business identities and effective dates for local manual versions."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def utc_iso(value: str | datetime) -> str:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Manual effective dates and as_of must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class ManualVersion:
    logical_doc_id: str
    model: str
    version: str
    effective_from: str
    effective_to: str | None
    source_path: str
    source_hash: str
    title: str
    source_url: str
    license: str
    fault_code: str | None = None
    publication_status: str = "staged"
    chunk_count: int = 0

    @property
    def namespace(self) -> str:
        identity = f"{self.logical_doc_id}\0{self.version}\0{self.source_hash}"
        return "manual_" + hashlib.sha256(identity.encode()).hexdigest()[:24]


class ManualRegistry:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS manual_versions (
                logical_doc_id TEXT NOT NULL,
                model TEXT NOT NULL,
                fault_code TEXT,
                version TEXT NOT NULL,
                effective_from TEXT NOT NULL,
                effective_to TEXT,
                source_path TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                license TEXT NOT NULL,
                publication_status TEXT NOT NULL CHECK(publication_status IN ('staged','published','error','deleting')),
                chunk_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (logical_doc_id, version)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_model_dates ON manual_versions(model, effective_from, effective_to)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> ManualVersion | None:
        return ManualVersion(**dict(row)) if row else None

    def get(self, logical_doc_id: str, version: str) -> ManualVersion | None:
        with self._connect() as conn:
            return self._from_row(conn.execute(
                "SELECT * FROM manual_versions WHERE logical_doc_id=? AND version=?",
                (logical_doc_id, version),
            ).fetchone())

    def list(self) -> list[ManualVersion]:
        with self._connect() as conn:
            return [self._from_row(r) for r in conn.execute(
                "SELECT * FROM manual_versions ORDER BY model, logical_doc_id, effective_from"
            ).fetchall()]

    def stage(self, doc: ManualVersion) -> ManualVersion:
        start = utc_iso(doc.effective_from)
        end = utc_iso(doc.effective_to) if doc.effective_to else None
        if end is not None and end <= start:
            raise ValueError("effective_to must be later than effective_from")
        doc = ManualVersion(**{**doc.__dict__, "effective_from": start, "effective_to": end})
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM manual_versions WHERE logical_doc_id=? AND version=?",
                (doc.logical_doc_id, doc.version),
            ).fetchone()
            if existing:
                previous = self._from_row(existing)
                if previous.source_hash != doc.source_hash or previous.model != doc.model:
                    raise ValueError("Version identity already exists with different content or model")
                if previous.publication_status == "published":
                    return previous
                if previous.publication_status == "deleting":
                    raise ValueError("Version deletion is pending")
            other = conn.execute(
                "SELECT * FROM manual_versions WHERE logical_doc_id=? AND version<>? AND publication_status IN ('staged','published')",
                (doc.logical_doc_id, doc.version),
            ).fetchall()
            for row in other:
                current = self._from_row(row)
                if current.model != doc.model:
                    raise ValueError("One logical manual cannot change model")
                if (end is None or current.effective_from < end) and (current.effective_to is None or start < current.effective_to):
                    raise ValueError(f"Effective dates overlap with {current.version}")
            if existing:
                conn.execute("UPDATE manual_versions SET publication_status='staged', chunk_count=0 WHERE logical_doc_id=? AND version=?",
                             (doc.logical_doc_id, doc.version))
            else:
                conn.execute("""INSERT INTO manual_versions
                    (logical_doc_id, model, fault_code, version, effective_from, effective_to,
                     source_path, source_hash, title, source_url, license, publication_status, chunk_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged', 0)""",
                    (doc.logical_doc_id, doc.model, doc.fault_code, doc.version, start, end,
                     doc.source_path, doc.source_hash, doc.title, doc.source_url, doc.license))
        return self.get(doc.logical_doc_id, doc.version)

    def set_status(self, doc: ManualVersion, status: str, chunk_count: int = 0) -> None:
        if status not in {"published", "error", "deleting"}:
            raise ValueError("Unsupported status")
        with self._connect() as conn:
            conn.execute("UPDATE manual_versions SET publication_status=?, chunk_count=? WHERE logical_doc_id=? AND version=?",
                         (status, chunk_count, doc.logical_doc_id, doc.version))

    def remove(self, doc: ManualVersion) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM manual_versions WHERE logical_doc_id=? AND version=? AND publication_status='deleting'",
                         (doc.logical_doc_id, doc.version))

    def select(self, model: str, as_of: str | datetime | None = None, fault_code: str | None = None) -> list[ManualVersion]:
        at = utc_iso(as_of or datetime.now(timezone.utc))
        with self._connect() as conn:
            rows = conn.execute("""SELECT * FROM manual_versions
                WHERE model=? AND publication_status='published' AND effective_from<=?
                  AND (effective_to IS NULL OR effective_to>?)
                  AND (fault_code IS NULL OR fault_code=?)
                ORDER BY logical_doc_id, effective_from DESC""", (model, at, at, fault_code)).fetchall()
        return [self._from_row(row) for row in rows]
