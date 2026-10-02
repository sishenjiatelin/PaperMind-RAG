"""Validate three CSVs and atomically activate a complete SQLite snapshot."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .schema import connect

SCHEMA_VERSION = "m0-v1"
ASSET_FIELDS = ("asset_id", "model", "site")
ORDER_FIELDS = (
    "work_order_id", "asset_id", "fault_code", "priority",
    "opened_at", "resolved_at", "status",
)
POLICY_FIELDS = (
    "policy_id", "priority", "effective_from", "effective_to", "resolution_hours",
)
MODELS = frozenset(("TAZ_PRO", "TAZ_WORKHORSE"))
SITES = frozenset(("EAST", "SOUTH", "WEST"))
FAULT_CODES = frozenset((
    "NOZZLE_WIPE", "HOTEND_TEMP", "FILAMENT_FEED", "BED_ADHESION", "MOTION_AXIS",
))
PRIORITIES = frozenset(("P1", "P2", "P3"))
STATUSES = frozenset(("open", "resolved"))
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class QualityIssue:
    source_file: str
    row_number: int | None
    rule_id: str
    severity: str
    message: str


@dataclass(frozen=True)
class ImportResult:
    status: str
    batch_id: str
    snapshot_id: str | None
    source_hash: str
    schema_version: str
    row_counts: dict[str, dict[str, int]]
    file_hashes: dict[str, str | None]
    issues: list[QualityIssue]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["rule_counts"] = dict(Counter(issue.rule_id for issue in self.issues))
        return result


@dataclass
class CsvInput:
    label: str
    source_file: str
    rows: list[dict[str, str]]
    sha256: str | None
    total_rows: int = 0


def _issue(issues: list[QualityIssue], source: str, row: int | None, rule: str, message: str) -> None:
    issues.append(QualityIssue(source, row, rule, "error", message))


def _read_csv(path: Path, label: str, fields: tuple[str, ...], issues: list[QualityIssue]) -> CsvInput:
    source = str(path.resolve())
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _issue(issues, source, None, "MISSING_OR_UNREADABLE_FILE", str(exc))
        return CsvInput(label, source, [], None)
    sha = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        _issue(issues, source, None, "INVALID_UTF8", "CSV must be UTF-8")
        return CsvInput(label, source, [], sha)
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if reader.fieldnames is None or tuple(reader.fieldnames) != fields:
            _issue(issues, source, 1, "INVALID_HEADER", f"expected {','.join(fields)}")
            return CsvInput(label, source, [], sha, sum(1 for _ in reader))
        rows = []
        total_rows = 0
        for row in reader:
            row_number = reader.line_num
            total_rows += 1
            if None in row or any(value is None for value in row.values()):
                _issue(issues, source, row_number, "INVALID_ROW_WIDTH", "field count differs from header")
                continue
            rows.append({**row, "__row_number__": str(row_number)})
        return CsvInput(label, source, rows, sha, total_rows)
    except csv.Error as exc:
        _issue(issues, source, None, "INVALID_CSV", str(exc))
        return CsvInput(label, source, [], sha)


def _required(row: dict[str, str], field: str, source: str, number: int, issues: list[QualityIssue]) -> str | None:
    value = row[field]
    if not value or value != value.strip():
        _issue(issues, source, number, "INVALID_REQUIRED_FIELD", f"{field} is empty or has outer whitespace")
        return None
    return value


def _timestamp(value: str, source: str, number: int, field: str, issues: list[QualityIssue]) -> tuple[str, int] | None:
    try:
        if "T" not in value:
            raise ValueError("timestamp must contain T")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone offset is required")
        utc = parsed.astimezone(timezone.utc)
        delta = utc - EPOCH
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        return utc.isoformat(timespec="auto").replace("+00:00", "Z"), micros
    except (ValueError, OverflowError) as exc:
        _issue(issues, source, number, "INVALID_TIMESTAMP", f"{field}: {exc}")
        return None


def _assets(data: CsvInput, issues: list[QualityIssue]) -> list[dict[str, Any]]:
    result = []
    seen: set[str] = set()
    for row in data.rows:
        number = int(row["__row_number__"])
        start = len(issues)
        asset_id = _required(row, "asset_id", data.source_file, number, issues)
        model = _required(row, "model", data.source_file, number, issues)
        site = _required(row, "site", data.source_file, number, issues)
        if asset_id:
            if asset_id in seen:
                _issue(issues, data.source_file, number, "DUPLICATE_ASSET_ID", asset_id)
            seen.add(asset_id)
        if model and model not in MODELS:
            _issue(issues, data.source_file, number, "INVALID_MODEL", model)
        if site and site not in SITES:
            _issue(issues, data.source_file, number, "INVALID_SITE", site)
        if len(issues) == start:
            result.append({"asset_id": asset_id, "model": model, "site": site})
    return result


def _policies(data: CsvInput, issues: list[QualityIssue]) -> list[dict[str, Any]]:
    result = []
    seen: set[str] = set()
    for row in data.rows:
        number = int(row["__row_number__"])
        start = len(issues)
        policy_id = _required(row, "policy_id", data.source_file, number, issues)
        priority = _required(row, "priority", data.source_file, number, issues)
        if policy_id:
            if policy_id in seen:
                _issue(issues, data.source_file, number, "DUPLICATE_POLICY_ID", policy_id)
            seen.add(policy_id)
        if priority and priority not in PRIORITIES:
            _issue(issues, data.source_file, number, "INVALID_PRIORITY", priority)
        effective_from = _timestamp(row["effective_from"], data.source_file, number, "effective_from", issues)
        effective_to = _timestamp(row["effective_to"], data.source_file, number, "effective_to", issues) if row["effective_to"] else None
        try:
            hours = int(row["resolution_hours"])
            if hours <= 0 or str(hours) != row["resolution_hours"]:
                raise ValueError
        except ValueError:
            _issue(issues, data.source_file, number, "INVALID_RESOLUTION_HOURS", row["resolution_hours"])
            hours = None
        if effective_from and effective_to and effective_to[1] <= effective_from[1]:
            _issue(issues, data.source_file, number, "INVALID_POLICY_INTERVAL", "end must follow start")
        if len(issues) == start:
            result.append({
                "policy_id": policy_id, "priority": priority,
                "effective_from": effective_from[0], "effective_from_us": effective_from[1],
                "effective_to": effective_to[0] if effective_to else None,
                "effective_to_us": effective_to[1] if effective_to else None,
                "resolution_hours": hours, "__row_number__": number,
            })
    for index, left in enumerate(result):
        for right in result[index + 1:]:
            if left["priority"] != right["priority"]:
                continue
            left_end = left["effective_to_us"] if left["effective_to_us"] is not None else 2**63 - 1
            right_end = right["effective_to_us"] if right["effective_to_us"] is not None else 2**63 - 1
            if left["effective_from_us"] < right_end and right["effective_from_us"] < left_end:
                _issue(issues, data.source_file, right["__row_number__"], "OVERLAPPING_SLA_POLICY", f"{left['policy_id']} overlaps {right['policy_id']}")
    return result


def _orders(
    data: CsvInput,
    assets: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    issues: list[QualityIssue],
) -> list[dict[str, Any]]:
    result = []
    asset_ids = {item["asset_id"] for item in assets}
    seen: set[str] = set()
    for row in data.rows:
        number = int(row["__row_number__"])
        source = row.get("__source_file__", data.source_file)
        start = len(issues)
        order_id = _required(row, "work_order_id", source, number, issues)
        asset_id = _required(row, "asset_id", source, number, issues)
        fault = _required(row, "fault_code", source, number, issues)
        priority = _required(row, "priority", source, number, issues)
        status = _required(row, "status", source, number, issues)
        if order_id:
            if order_id in seen:
                _issue(issues, source, number, "DUPLICATE_WORK_ORDER_ID", order_id)
            seen.add(order_id)
        if asset_id and asset_id not in asset_ids:
            _issue(issues, source, number, "UNKNOWN_ASSET", asset_id)
        if fault and fault not in FAULT_CODES:
            _issue(issues, source, number, "INVALID_FAULT_CODE", fault)
        if priority and priority not in PRIORITIES:
            _issue(issues, source, number, "INVALID_PRIORITY", priority)
        if status and status not in STATUSES:
            _issue(issues, source, number, "INVALID_STATUS", status)
        opened = _timestamp(row["opened_at"], source, number, "opened_at", issues)
        resolved = _timestamp(row["resolved_at"], source, number, "resolved_at", issues) if row["resolved_at"] else None
        if status == "resolved" and not row["resolved_at"]:
            _issue(issues, source, number, "RESOLVED_WITHOUT_TIMESTAMP", order_id or "")
        if status == "open" and row["resolved_at"]:
            _issue(issues, source, number, "OPEN_WITH_RESOLVED_TIMESTAMP", order_id or "")
        if opened and resolved and resolved[1] < opened[1]:
            _issue(issues, source, number, "RESOLVED_BEFORE_OPENED", order_id or "")
        applicable = []
        if opened and priority in PRIORITIES:
            applicable = [policy for policy in policies
                if policy["priority"] == priority
                and policy["effective_from_us"] <= opened[1]
                and (policy["effective_to_us"] is None or opened[1] < policy["effective_to_us"])]
            if len(applicable) != 1:
                rule = "MISSING_SLA_POLICY" if not applicable else "AMBIGUOUS_SLA_POLICY"
                _issue(issues, source, number, rule, f"{priority} at {opened[0]}")
        if len(issues) == start:
            policy = applicable[0]
            due_us = opened[1] + policy["resolution_hours"] * 3_600_000_000
            due = EPOCH + timedelta(microseconds=due_us)
            result.append({
                "work_order_id": order_id, "asset_id": asset_id,
                "fault_code": fault, "priority": priority,
                "opened_at": opened[0], "opened_at_us": opened[1],
                "resolved_at": resolved[0] if resolved else None,
                "resolved_at_us": resolved[1] if resolved else None,
                "due_at": due.isoformat(timespec="auto").replace("+00:00", "Z"),
                "due_at_us": due_us, "status": status,
                "policy_id": policy["policy_id"], "__source_file__": source,
            })
    return result


def _counts(inputs: list[CsvInput], issues: list[QualityIssue]) -> dict[str, dict[str, int]]:
    result = {}
    for item in inputs:
        bad_rows = {issue.row_number for issue in issues
                    if issue.source_file == item.source_file and issue.row_number is not None and issue.row_number >= 2}
        invalid_header = any(issue.source_file == item.source_file and issue.rule_id == "INVALID_HEADER"
                             for issue in issues)
        rejected = item.total_rows if invalid_header else len(bad_rows)
        result[item.label] = {
            "total": item.total_rows,
            "valid": item.total_rows - rejected,
            "rejected": rejected,
            "imported": 0,
        }
    return result


def _source_hash(inputs: list[CsvInput]) -> str:
    digest = hashlib.sha256(SCHEMA_VERSION.encode())
    for item in inputs:
        digest.update(item.label.encode())
        digest.update((item.sha256 or "MISSING").encode())
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def import_snapshot(
    db_path: str | Path,
    data_dir: str | Path,
    *,
    work_orders_path: str | Path | None = None,
    extra_work_orders_path: str | Path | None = None,
) -> ImportResult:
    """Import a complete, validated snapshot; rejected inputs never replace active data."""
    folder = Path(data_dir)
    specs = (
        (folder / "assets.csv", "assets.csv", ASSET_FIELDS),
        (folder / "sla_policies.csv", "sla_policies.csv", POLICY_FIELDS),
        (Path(work_orders_path) if work_orders_path else folder / "work_orders.csv", "work_orders.csv", ORDER_FIELDS),
    )
    issues: list[QualityIssue] = []
    inputs = [_read_csv(path, label, fields, issues) for path, label, fields in specs]
    assets = _assets(inputs[0], issues)
    policies = _policies(inputs[1], issues)
    order_rows = list(inputs[2].rows)
    if extra_work_orders_path is not None:
        extra = _read_csv(Path(extra_work_orders_path), "extra_work_orders.csv", ORDER_FIELDS, issues)
        inputs.append(extra)
        order_rows.extend({**row, "__source_file__": extra.source_file} for row in extra.rows)
    combined = CsvInput("work_orders.csv", inputs[2].source_file, order_rows, None)
    orders = _orders(combined, assets, policies, issues)
    counts = _counts(inputs, issues)
    file_hashes = {item.label: item.sha256 for item in inputs}
    source_hash = _source_hash(inputs)
    connection = connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT batch_id FROM ingestion_batches WHERE source_hash = ? AND status = 'imported'",
            (source_hash,),
        ).fetchone()
        if existing:
            active = connection.execute("SELECT snapshot_id FROM active_snapshot WHERE singleton_id = 1").fetchone()
            connection.commit()
            return ImportResult(
                "noop", existing["batch_id"], active["snapshot_id"] if active else None,
                source_hash, SCHEMA_VERSION, counts, file_hashes, [],
            )
        batch_id = "batch-" + uuid.uuid4().hex
        status = "rejected" if issues else "imported"
        if not issues:
            for item in inputs:
                counts[item.label]["imported"] = counts[item.label]["valid"]
        now = _now()
        connection.execute(
            "INSERT INTO ingestion_batches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_id, source_hash, now, status, SCHEMA_VERSION,
             json.dumps(counts, sort_keys=True), json.dumps(file_hashes, sort_keys=True), len(issues)),
        )
        connection.executemany(
            "INSERT INTO data_quality_issues(batch_id, source_file, row_number, rule_id, severity, message) VALUES (?, ?, ?, ?, ?, ?)",
            [(batch_id, item.source_file, item.row_number, item.rule_id, item.severity, item.message) for item in issues],
        )
        if not issues:
            connection.executemany(
                "INSERT INTO assets(snapshot_id, asset_id, model, site) VALUES (?, ?, ?, ?)",
                [(batch_id, row["asset_id"], row["model"], row["site"]) for row in assets],
            )
            connection.executemany(
                "INSERT INTO sla_policies(snapshot_id, policy_id, priority, effective_from, effective_to, effective_from_us, effective_to_us, resolution_hours) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(batch_id, row["policy_id"], row["priority"], row["effective_from"],
                  row["effective_to"], row["effective_from_us"], row["effective_to_us"], row["resolution_hours"])
                 for row in policies],
            )
            connection.executemany(
                "INSERT INTO work_orders(snapshot_id, work_order_id, asset_id, fault_code, priority, opened_at, resolved_at, due_at, opened_at_us, resolved_at_us, due_at_us, status, policy_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(batch_id, row["work_order_id"], row["asset_id"], row["fault_code"],
                  row["priority"], row["opened_at"], row["resolved_at"], row["due_at"],
                  row["opened_at_us"], row["resolved_at_us"], row["due_at_us"],
                  row["status"], row["policy_id"]) for row in orders],
            )
            connection.execute(
                "INSERT INTO active_snapshot(singleton_id, snapshot_id, activated_at) VALUES (1, ?, ?) ON CONFLICT(singleton_id) DO UPDATE SET snapshot_id = excluded.snapshot_id, activated_at = excluded.activated_at",
                (batch_id, now),
            )
        connection.commit()
        active = connection.execute("SELECT snapshot_id FROM active_snapshot WHERE singleton_id = 1").fetchone()
        return ImportResult(status, batch_id, active["snapshot_id"] if active else None,
                            source_hash, SCHEMA_VERSION, counts, file_hashes, issues)
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_active_snapshot(db_path: str | Path) -> dict[str, Any] | None:
    connection = connect(db_path, read_only=True)
    try:
        row = connection.execute(
            "SELECT a.snapshot_id, a.activated_at, b.source_hash, b.schema_version, b.row_counts_json FROM active_snapshot a JOIN ingestion_batches b ON b.batch_id = a.snapshot_id WHERE a.singleton_id = 1"
        ).fetchone()
        if not row:
            return None
        return {"snapshot_id": row["snapshot_id"], "activated_at": row["activated_at"],
                "source_hash": row["source_hash"], "schema_version": row["schema_version"],
                "row_counts": json.loads(row["row_counts_json"])}
    finally:
        connection.close()


def get_batch_report(db_path: str | Path, batch_id: str) -> dict[str, Any] | None:
    connection = connect(db_path, read_only=True)
    try:
        batch = connection.execute("SELECT * FROM ingestion_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        if not batch:
            return None
        issues = connection.execute(
            "SELECT source_file, row_number, rule_id, severity, message FROM data_quality_issues WHERE batch_id = ? ORDER BY issue_id",
            (batch_id,),
        ).fetchall()
        return {
            "batch_id": batch["batch_id"], "source_hash": batch["source_hash"],
            "imported_at": batch["imported_at"], "status": batch["status"],
            "schema_version": batch["schema_version"],
            "row_counts": json.loads(batch["row_counts_json"]),
            "file_hashes": json.loads(batch["file_hashes_json"]),
            "issue_count": batch["issue_count"],
            "rule_counts": dict(Counter(row["rule_id"] for row in issues)),
            "issues": [dict(row) for row in issues],
        }
    finally:
        connection.close()
