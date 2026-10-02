"""SQLite schema and connection helpers for complete service data snapshots."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ingestion_batches (
    batch_id TEXT PRIMARY KEY,
    source_hash TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('imported', 'rejected')),
    schema_version TEXT NOT NULL,
    row_counts_json TEXT NOT NULL,
    file_hashes_json TEXT NOT NULL,
    issue_count INTEGER NOT NULL CHECK (issue_count >= 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_imported_source
    ON ingestion_batches(source_hash) WHERE status = 'imported';
CREATE TABLE IF NOT EXISTS active_snapshot (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    snapshot_id TEXT NOT NULL REFERENCES ingestion_batches(batch_id),
    activated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    snapshot_id TEXT NOT NULL REFERENCES ingestion_batches(batch_id),
    asset_id TEXT NOT NULL,
    model TEXT NOT NULL CHECK (model IN ('TAZ_PRO', 'TAZ_WORKHORSE')),
    site TEXT NOT NULL CHECK (site IN ('EAST', 'SOUTH', 'WEST')),
    PRIMARY KEY (snapshot_id, asset_id)
);
CREATE TABLE IF NOT EXISTS sla_policies (
    snapshot_id TEXT NOT NULL REFERENCES ingestion_batches(batch_id),
    policy_id TEXT NOT NULL,
    priority TEXT NOT NULL CHECK (priority IN ('P1', 'P2', 'P3')),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    effective_from_us INTEGER NOT NULL,
    effective_to_us INTEGER,
    resolution_hours INTEGER NOT NULL CHECK (resolution_hours > 0),
    PRIMARY KEY (snapshot_id, policy_id),
    CHECK (effective_to_us IS NULL OR effective_to_us > effective_from_us)
);
CREATE TABLE IF NOT EXISTS work_orders (
    snapshot_id TEXT NOT NULL,
    work_order_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    fault_code TEXT NOT NULL,
    priority TEXT NOT NULL CHECK (priority IN ('P1', 'P2', 'P3')),
    opened_at TEXT NOT NULL,
    resolved_at TEXT,
    due_at TEXT NOT NULL,
    opened_at_us INTEGER NOT NULL,
    resolved_at_us INTEGER,
    due_at_us INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
    policy_id TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, work_order_id),
    FOREIGN KEY (snapshot_id, asset_id) REFERENCES assets(snapshot_id, asset_id),
    FOREIGN KEY (snapshot_id, policy_id) REFERENCES sla_policies(snapshot_id, policy_id),
    CHECK ((status = 'open' AND resolved_at IS NULL AND resolved_at_us IS NULL)
        OR (status = 'resolved' AND resolved_at IS NOT NULL AND resolved_at_us >= opened_at_us))
);
CREATE INDEX IF NOT EXISTS ix_orders_resolved
    ON work_orders(snapshot_id, resolved_at_us);
CREATE INDEX IF NOT EXISTS ix_orders_due
    ON work_orders(snapshot_id, due_at_us);
CREATE INDEX IF NOT EXISTS ix_orders_opened
    ON work_orders(snapshot_id, opened_at_us);
CREATE INDEX IF NOT EXISTS ix_repeat_lookup
    ON work_orders(snapshot_id, asset_id, fault_code, resolved_at_us);
CREATE TABLE IF NOT EXISTS data_quality_issues (
    issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL REFERENCES ingestion_batches(batch_id),
    source_file TEXT NOT NULL,
    row_number INTEGER,
    rule_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('error', 'warning')),
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_quality_batch ON data_quality_issues(batch_id);
"""


def connect(db_path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open the separate service database; readers cannot write through this handle."""
    path = Path(db_path).resolve()
    if read_only:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
        connection.execute("PRAGMA query_only = ON")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.executescript(SCHEMA_SQL)
    connection.row_factory = sqlite3.Row
    return connection
