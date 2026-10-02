"""Read-only service for the Service Operations dashboard.

All metric values come from the M1 query registry. The page never constructs SQL.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from service_operations.contracts import QueryPeriod, parse_instant
from service_operations.metrics.query import METRIC_IDS, query_metric
from service_operations.warehouse.importer import FAULT_CODES, MODELS, SITES, get_batch_report
from service_operations.warehouse.schema import connect

SHANGHAI = ZoneInfo("Asia/Shanghai")
GROUP_FIELDS = {"model": sorted(MODELS), "site": sorted(SITES), "fault_code": sorted(FAULT_CODES)}
FEATURE_ROOT = Path(__file__).resolve().parent
DEFAULT_DB = FEATURE_ROOT / "runtime/service_operations.db"
DEFAULT_INDEX = FEATURE_ROOT / "runtime/manual_index"


def _next_month(value: datetime) -> datetime:
    year = value.year + (value.month == 12)
    month = 1 if value.month == 12 else value.month + 1
    return value.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _shift_month(value: date, months: int) -> date:
    number = value.year * 12 + value.month - 1 + months
    return date(number // 12, number % 12 + 1, 1)


class ServiceOpsDashboardData:
    def __init__(self, db_path: str | Path = DEFAULT_DB,
                 manual_index_dir: str | Path = DEFAULT_INDEX):
        self.db_path = Path(db_path)
        self.manual_index_dir = Path(manual_index_dir)

    def status(self) -> dict[str, Any]:
        if not self.db_path.is_file():
            return {"ready": False, "reason": "SERVICE_DB_MISSING"}
        try:
            connection = connect(self.db_path, read_only=True)
            try:
                active = connection.execute("""SELECT a.snapshot_id, a.activated_at,
                    b.imported_at, b.schema_version, b.source_hash
                    FROM active_snapshot a JOIN ingestion_batches b ON b.batch_id=a.snapshot_id
                    WHERE a.singleton_id=1""").fetchone()
                latest = connection.execute("""SELECT batch_id, status, imported_at, issue_count
                    FROM ingestion_batches ORDER BY imported_at DESC, rowid DESC LIMIT 1""").fetchone()
                total_issues = connection.execute("SELECT COUNT(*) FROM data_quality_issues").fetchone()[0]
                batch_counts = {row["status"]: row["n"] for row in connection.execute(
                    "SELECT status, COUNT(*) AS n FROM ingestion_batches GROUP BY status"
                )}
                if not active:
                    return {"ready": False, "reason": "NO_ACTIVE_SNAPSHOT",
                            "latest_batch": dict(latest) if latest else None,
                            "quality_issue_count": total_issues, "batch_counts": batch_counts}
                snapshot_id = active["snapshot_id"]
                counts = {table: connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE snapshot_id=?", (snapshot_id,)
                ).fetchone()[0] for table in ("assets", "work_orders", "sla_policies")}
                bounds = connection.execute("""SELECT MIN(opened_at_us) AS first_us,
                    MAX(opened_at_us) AS last_us FROM work_orders WHERE snapshot_id=?""",
                    (snapshot_id,)).fetchone()
                period = None
                if bounds["last_us"] is not None:
                    last = datetime.fromtimestamp(bounds["last_us"] / 1_000_000, timezone.utc).astimezone(SHANGHAI)
                    end = _shift_month(last.date(), 1)
                    period = {"start": _shift_month(end, -3).isoformat(), "end": end.isoformat()}
                return {"ready": True, "snapshot_id": snapshot_id,
                        "activated_at": active["activated_at"], "imported_at": active["imported_at"],
                        "schema_version": active["schema_version"], "source_hash": active["source_hash"],
                        "row_counts": counts, "quality_issue_count": total_issues,
                        "batch_counts": batch_counts, "latest_batch": dict(latest) if latest else None,
                        "default_period": period}
            finally:
                connection.close()
        except (sqlite3.Error, OSError) as exc:
            return {"ready": False, "reason": "SERVICE_DB_UNAVAILABLE", "detail": str(exc)}

    def batches(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.db_path.is_file():
            return []
        limit = max(1, min(limit, 50))
        connection = connect(self.db_path, read_only=True)
        try:
            rows = connection.execute("""SELECT batch_id, status, imported_at,
                issue_count, schema_version, row_counts_json
                FROM ingestion_batches ORDER BY imported_at DESC, rowid DESC LIMIT ?""",
                (limit,)).fetchall()
            return [{**dict(row), "row_counts": json.loads(row["row_counts_json"])}
                    for row in rows]
        finally:
            connection.close()

    def batch_report(self, batch_id: str) -> dict[str, Any] | None:
        return get_batch_report(self.db_path, batch_id)

    def manual_versions(self) -> list[dict[str, Any]]:
        db = self.manual_index_dir / "registry.db"
        if not db.is_file():
            return []
        from service_operations.manuals.search import ManualIndex

        index = ManualIndex(self.manual_index_dir)
        results = []
        now = datetime.now(timezone.utc)
        for doc in index.registry.list():
            bm25_ready = (index.bm25_dir / f"{doc.namespace}_bm25.json").is_file()
            try:
                chroma_count = index.chroma.get_collection(doc.namespace).count()
            except Exception:
                chroma_count = None
            index_ready = (doc.publication_status == "published" and bm25_ready
                           and chroma_count == doc.chunk_count and doc.chunk_count > 0)
            start = parse_instant(doc.effective_from)
            end = parse_instant(doc.effective_to) if doc.effective_to else None
            results.append({"logical_doc_id": doc.logical_doc_id, "model": doc.model,
                            "fault_code": doc.fault_code, "version": doc.version,
                            "effective_from": doc.effective_from, "effective_to": doc.effective_to,
                            "title": doc.title, "source": doc.source_url or doc.source_path,
                            "source_hash": doc.source_hash, "license": doc.license,
                            "publication_status": doc.publication_status,
                            "chunk_count": doc.chunk_count, "chroma_count": chroma_count,
                            "bm25_ready": bm25_ready, "index_ready": index_ready,
                            "current": index_ready and start <= now and (end is None or now < end)})
        return results

    def trend(self, metric_id: str, period: QueryPeriod, *, as_of: str,
              snapshot_id: str, group_by: str = "all", model: str | None = None,
              site: str | None = None, fault_code: str | None = None) -> list[dict[str, Any]]:
        if metric_id not in METRIC_IDS:
            raise ValueError(f"Unknown metric_id: {metric_id}")
        if group_by not in {"all", *GROUP_FIELDS}:
            raise ValueError(f"Unknown group_by: {group_by}")
        start, end = period.bounds()
        if end > parse_instant(as_of):
            raise ValueError("period ends after as_of")
        begin_local = start.astimezone(SHANGHAI)
        end_local = end.astimezone(SHANGHAI)
        selected_filters = {"model": model, "site": site, "fault_code": fault_code}
        if group_by == "all":
            groups = ["全部"]
        elif selected_filters[group_by]:
            groups = [selected_filters[group_by]]
        else:
            groups = GROUP_FIELDS[group_by]
        rows = []
        bucket_start = begin_local
        while bucket_start < end_local:
            month_start = bucket_start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            bucket_end = min(_next_month(month_start), end_local)
            for group in groups:
                filters = {"model": model, "site": site, "fault_code": fault_code}
                if group_by != "all":
                    filters[group_by] = group
                metric = query_metric(
                    self.db_path, metric_id, bucket_start.isoformat(), bucket_end.isoformat(),
                    as_of=as_of, snapshot_id=snapshot_id, **filters,
                )
                rows.append({"period_start": bucket_start.isoformat(),
                             "period_end": bucket_end.isoformat(),
                             "period_label": bucket_start.strftime("%Y-%m"),
                             "group": group, "value": metric["value"],
                             "display_value": (metric["value"] * 100 if metric["unit"] == "ratio"
                                               and metric["value"] is not None else metric["value"]),
                             "numerator": metric["numerator"],
                             "denominator": metric["denominator"], "metric": metric})
            bucket_start = bucket_end
            if len(rows) > 120:
                raise ValueError("Trend request exceeds 120 group-month points")
        return rows

    def metric_details(self, metric: dict[str, Any], *, offset: int = 0,
                       limit: int = 100) -> dict[str, Any]:
        ids = metric["eligible_work_order_ids"]
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError("Invalid detail pagination")
        selected = ids[offset:offset + limit]
        if not selected:
            return {"total": len(ids), "offset": offset, "rows": []}
        placeholders = ",".join("?" for _ in selected)
        connection = connect(self.db_path, read_only=True)
        try:
            rows = connection.execute(f"""SELECT w.work_order_id, w.asset_id, a.model, a.site,
                w.fault_code, w.priority, w.status, w.opened_at, w.resolved_at,
                w.due_at, w.policy_id, w.opened_at_us, w.resolved_at_us
                FROM work_orders w JOIN assets a
                  ON a.snapshot_id=w.snapshot_id AND a.asset_id=w.asset_id
                WHERE w.snapshot_id=? AND w.work_order_id IN ({placeholders})
                ORDER BY w.work_order_id""", (metric["snapshot_id"], *selected)).fetchall()
        finally:
            connection.close()
        successes = set(metric["success_work_order_ids"] or [])
        predecessors = metric["prior_work_order_ids"] or {}
        details = []
        for raw in rows:
            row = dict(raw)
            if metric["metric_id"] == "mttr_hours":
                row["metric_outcome"] = round((row["resolved_at_us"] - row["opened_at_us"]) / 3_600_000_000, 6)
                row["metric_outcome_unit"] = "hours"
            else:
                row["metric_outcome"] = row["work_order_id"] in successes
                row["metric_outcome_unit"] = "bool"
            row["prior_work_order_id"] = predecessors.get(row["work_order_id"])
            del row["opened_at_us"]
            del row["resolved_at_us"]
            details.append(row)
        return {"total": len(ids), "offset": offset, "rows": details}
