"""CSV-only oracle for the three v1 metrics; no production SQL is imported."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _instant(value: str) -> datetime:
    if len(value) == 10:
        return datetime.fromisoformat(value).replace(tzinfo=TZ).astimezone(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class CsvReference:
    def __init__(self, directory: str | Path):
        root = Path(directory)
        self.assets = {row["asset_id"]: row for row in _rows(root / "assets.csv")}
        self.orders = _rows(root / "work_orders.csv")
        self.policies = _rows(root / "sla_policies.csv")
        for row in self.orders:
            row["_opened"] = _instant(row["opened_at"])
            row["_resolved"] = _instant(row["resolved_at"]) if row["resolved_at"] else None

    def calculate(self, metric_id: str, start: str, end: str, as_of: str,
                  filters: dict[str, str]) -> dict:
        lower, upper, cutoff = _instant(start), _instant(end), _instant(as_of)
        eligible, successes = [], []
        duration = 0.0
        for row in self.orders:
            asset = self.assets[row["asset_id"]]
            if any((asset[key] if key in {"model", "site"} else row[key]) != value
                   for key, value in filters.items()):
                continue
            opened, resolved = row["_opened"], row["_resolved"]
            if metric_id == "mttr_hours":
                if resolved is None or not lower <= resolved < upper:
                    continue
                duration += (resolved - opened).total_seconds() / 3600
            elif metric_id == "sla_attainment":
                policies = [policy for policy in self.policies
                            if policy["priority"] == row["priority"]
                            and _instant(policy["effective_from"]) <= opened
                            and (not policy["effective_to"] or opened < _instant(policy["effective_to"]))]
                if len(policies) != 1:
                    raise ValueError(f"Expected one SLA policy: {row['work_order_id']}")
                due = opened + timedelta(hours=int(policies[0]["resolution_hours"]))
                if not lower <= due < upper or due > cutoff:
                    continue
                if resolved is not None and resolved <= due:
                    successes.append(row["work_order_id"])
            elif metric_id == "repeat_fault_rate_30d":
                if not lower <= opened < upper:
                    continue
                if any(prior["asset_id"] == row["asset_id"]
                       and prior["fault_code"] == row["fault_code"]
                       and prior["_resolved"] is not None
                       and opened - timedelta(days=30) <= prior["_resolved"] < opened
                       for prior in self.orders):
                    successes.append(row["work_order_id"])
            else:
                raise ValueError(f"Unknown metric: {metric_id}")
            eligible.append(row["work_order_id"])
        denominator = len(eligible)
        numerator = round(duration, 6) if metric_id == "mttr_hours" else len(successes)
        return {
            "value": round(numerator / denominator, 6) if denominator else None,
            "numerator": numerator, "denominator": denominator,
            "eligible_work_order_ids": sorted(eligible),
            "success_work_order_ids": sorted(successes) if metric_id != "mttr_hours" else None,
        }
