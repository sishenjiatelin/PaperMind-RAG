"""Three versioned metrics using only parameterized, predefined SQLite queries."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from service_operations.warehouse.importer import FAULT_CODES, MODELS, PRIORITIES, SITES
from service_operations.warehouse.schema import connect

TZ = ZoneInfo("Asia/Shanghai")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MICROS_PER_HOUR = 3_600_000_000
REPEAT_WINDOW_US = 720 * MICROS_PER_HOUR
METRIC_IDS = frozenset(("mttr_hours", "sla_attainment", "repeat_fault_rate_30d"))
FILTER_VALUES = {"model": MODELS, "site": SITES, "priority": PRIORITIES, "fault_code": FAULT_CODES}

BASE_WHERE = """
w.snapshot_id = :snapshot_id
AND (:model IS NULL OR a.model = :model)
AND (:site IS NULL OR a.site = :site)
AND (:priority IS NULL OR w.priority = :priority)
AND (:fault_code IS NULL OR w.fault_code = :fault_code)
"""

MTTR_CTE = f"""
WITH eligible AS (
    SELECT w.work_order_id, w.resolved_at_us - w.opened_at_us AS duration_us
    FROM work_orders w
    JOIN assets a ON a.snapshot_id = w.snapshot_id AND a.asset_id = w.asset_id
    WHERE {BASE_WHERE}
      AND w.status = 'resolved'
      AND w.resolved_at_us >= :start_us AND w.resolved_at_us < :end_us
)
"""
SLA_CTE = f"""
WITH eligible AS (
    SELECT w.work_order_id,
           CASE WHEN w.resolved_at_us <= w.due_at_us THEN 1 ELSE 0 END AS success
    FROM work_orders w
    JOIN assets a ON a.snapshot_id = w.snapshot_id AND a.asset_id = w.asset_id
    WHERE {BASE_WHERE}
      AND w.due_at_us >= :start_us AND w.due_at_us < :end_us
      AND w.due_at_us <= :as_of_us
)
"""
REPEAT_CTE = f"""
WITH eligible AS (
    SELECT w.work_order_id,
           (SELECT prior.work_order_id FROM work_orders prior
            WHERE prior.snapshot_id = w.snapshot_id
              AND prior.asset_id = w.asset_id
              AND prior.fault_code = w.fault_code
              AND prior.resolved_at_us >= w.opened_at_us - :repeat_window_us
              AND prior.resolved_at_us < w.opened_at_us
            ORDER BY prior.resolved_at_us DESC, prior.work_order_id DESC LIMIT 1
           ) AS prior_work_order_id
    FROM work_orders w
    JOIN assets a ON a.snapshot_id = w.snapshot_id AND a.asset_id = w.asset_id
    WHERE {BASE_WHERE}
      AND w.opened_at_us >= :start_us AND w.opened_at_us < :end_us
)
"""


def _instant(value: str, *, allow_date: bool) -> datetime:
    if allow_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=TZ)
    if "T" not in value:
        raise ValueError("Use YYYY-MM-DD or a timezone-aware ISO 8601 datetime")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Datetime must include a timezone offset")
    return parsed


def _epoch_us(value: datetime) -> int:
    delta = value.astimezone(timezone.utc) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def query_metric(
    db_path: str | Path,
    metric_id: str,
    start: str,
    end: str,
    *,
    as_of: str | None = None,
    snapshot_id: str | None = None,
    model: str | None = None,
    site: str | None = None,
    priority: str | None = None,
    fault_code: str | None = None,
) -> dict[str, Any]:
    """Return a reproducible metric and all eligible work-order IDs."""
    if metric_id not in METRIC_IDS:
        raise ValueError(f"Unknown metric_id: {metric_id}")
    filters = {"model": model, "site": site, "priority": priority, "fault_code": fault_code}
    for key, value in filters.items():
        if value is not None and value not in FILTER_VALUES[key]:
            raise ValueError(f"Invalid {key}: {value}")
    start_dt = _instant(start, allow_date=True)
    end_dt = _instant(end, allow_date=True)
    if end_dt <= start_dt:
        raise ValueError("end must be later than start")
    as_of_dt = _instant(as_of, allow_date=False) if as_of else datetime.now(timezone.utc)
    params: dict[str, Any] = {
        "start_us": _epoch_us(start_dt), "end_us": _epoch_us(end_dt),
        "as_of_us": _epoch_us(as_of_dt), "repeat_window_us": REPEAT_WINDOW_US,
        **filters,
    }
    connection = connect(db_path, read_only=True)
    try:
        if snapshot_id is None:
            active = connection.execute("SELECT snapshot_id FROM active_snapshot WHERE singleton_id = 1").fetchone()
            if not active:
                raise ValueError("No active snapshot")
            snapshot_id = active["snapshot_id"]
        else:
            valid = connection.execute(
                "SELECT 1 FROM ingestion_batches WHERE batch_id = ? AND status = 'imported'",
                (snapshot_id,),
            ).fetchone()
            if not valid:
                raise ValueError(f"Unknown imported snapshot: {snapshot_id}")
        params["snapshot_id"] = snapshot_id
        if metric_id == "mttr_hours":
            cte = MTTR_CTE
            aggregate = "SELECT COUNT(*) AS denominator, COALESCE(SUM(duration_us), 0) AS numerator FROM eligible"
            detail = "SELECT work_order_id, duration_us FROM eligible ORDER BY work_order_id"
        elif metric_id == "sla_attainment":
            cte = SLA_CTE
            aggregate = "SELECT COUNT(*) AS denominator, COALESCE(SUM(success), 0) AS numerator FROM eligible"
            detail = "SELECT work_order_id, success FROM eligible ORDER BY work_order_id"
        else:
            cte = REPEAT_CTE
            aggregate = "SELECT COUNT(*) AS denominator, COUNT(prior_work_order_id) AS numerator FROM eligible"
            detail = "SELECT work_order_id, prior_work_order_id FROM eligible ORDER BY work_order_id"
        aggregate_row = connection.execute(cte + aggregate, params).fetchone()
        detail_rows = connection.execute(cte + detail, params).fetchall()
        denominator = int(aggregate_row["denominator"])
        if denominator != len(detail_rows):
            raise RuntimeError("Metric detail count differs from SQL aggregate")
        if metric_id == "mttr_hours":
            numerator: float | int = round(int(aggregate_row["numerator"]) / MICROS_PER_HOUR, 6)
            successes = None
            predecessors = None
        elif metric_id == "sla_attainment":
            numerator = int(aggregate_row["numerator"])
            successes = [row["work_order_id"] for row in detail_rows if row["success"] == 1]
            predecessors = None
        else:
            numerator = int(aggregate_row["numerator"])
            successes = [row["work_order_id"] for row in detail_rows if row["prior_work_order_id"]]
            predecessors = {row["work_order_id"]: row["prior_work_order_id"] for row in detail_rows
                            if row["prior_work_order_id"]}
        return {
            "metric_id": metric_id,
            "definition_version": "v1",
            "value": round(numerator / denominator, 6) if denominator else None,
            "unit": "hours" if metric_id == "mttr_hours" else "ratio",
            "numerator": numerator,
            "denominator": denominator,
            "period": {
                "start": start, "end": end, "timezone": "Asia/Shanghai",
                "start_utc": start_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "end_utc": end_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
            "filters": {key: value for key, value in filters.items() if value is not None},
            "as_of": as_of_dt.isoformat(),
            "snapshot_id": snapshot_id,
            "eligible_work_order_ids": [row["work_order_id"] for row in detail_rows],
            "success_work_order_ids": successes,
            "prior_work_order_ids": predecessors,
            "warning": "NO_ELIGIBLE_RECORDS" if not denominator else None,
        }
    finally:
        connection.close()
