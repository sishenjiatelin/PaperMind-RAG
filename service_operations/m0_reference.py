"""Independent Python reference calculations for the M0 SQL metric cases."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def build_reference(orders, assets, policies, config):
    tz = ZoneInfo(config["timezone"])
    as_of = datetime.fromisoformat(config["as_of"])
    decoded = []
    for row in orders:
        decoded.append({
            **row,
            "opened": datetime.fromisoformat(row["opened_at"].replace("Z", "+00:00")),
            "resolved": datetime.fromisoformat(row["resolved_at"].replace("Z", "+00:00"))
            if row["resolved_at"] else None,
        })
    cases = [
        ("mttr_pro_q1", "mttr_hours", "2026-01-01", "2026-04-01", {"model": "TAZ_PRO"}),
        ("mttr_pro_q2", "mttr_hours", "2026-04-01", "2026-07-01", {"model": "TAZ_PRO"}),
        ("sla_pro_q2", "sla_attainment", "2026-04-01", "2026-07-01", {"model": "TAZ_PRO"}),
        ("repeat_pro_nozzle_prior", "repeat_fault_rate_30d", "2026-03-01", "2026-06-01", {"model": "TAZ_PRO", "fault_code": "NOZZLE_WIPE"}),
        ("repeat_pro_nozzle_recent", "repeat_fault_rate_30d", "2026-06-01", "2026-09-01", {"model": "TAZ_PRO", "fault_code": "NOZZLE_WIPE"}),
    ]
    results = []
    for case_id, metric_id, start_text, end_text, filters in cases:
        start = datetime.combine(date.fromisoformat(start_text), time.min, tzinfo=tz)
        end = datetime.combine(date.fromisoformat(end_text), time.min, tzinfo=tz)
        eligible = []
        successful = []
        total_seconds = 0
        for row in decoded:
            asset = assets[row["asset_id"]]
            if any((asset[key] if key in ("model", "site") else row[key]) != value for key, value in filters.items()):
                continue
            opened, resolved = row["opened"], row["resolved"]
            if metric_id == "mttr_hours":
                if resolved is not None and start <= resolved < end:
                    eligible.append(row["work_order_id"])
                    total_seconds += (resolved - opened).total_seconds()
            elif metric_id == "sla_attainment":
                applicable = [policy for policy in policies
                    if policy["priority"] == row["priority"]
                    and datetime.fromisoformat(policy["effective_from"].replace("Z", "+00:00")) <= opened
                    and (not policy["effective_to"] or opened < datetime.fromisoformat(policy["effective_to"].replace("Z", "+00:00")))]
                if len(applicable) != 1:
                    raise ValueError(f"Expected one SLA policy for {row['work_order_id']}")
                due = opened + timedelta(hours=int(applicable[0]["resolution_hours"]))
                if start <= due < end and due <= as_of:
                    eligible.append(row["work_order_id"])
                    if resolved is not None and resolved <= due:
                        successful.append(row["work_order_id"])
            elif start <= opened < end:
                eligible.append(row["work_order_id"])
                if any(previous["asset_id"] == row["asset_id"]
                    and previous["fault_code"] == row["fault_code"]
                    and previous["resolved"] is not None
                    and opened - timedelta(hours=720) <= previous["resolved"] < opened
                    for previous in decoded):
                    successful.append(row["work_order_id"])
        denominator = len(eligible)
        numerator = round(total_seconds / 3600, 2) if metric_id == "mttr_hours" else len(successful)
        results.append({
            "case_id": case_id,
            "metric_id": metric_id,
            "definition_version": "v1",
            "period": {"start": start_text, "end": end_text, "timezone": str(tz)},
            "filters": filters,
            "as_of": config["as_of"],
            "numerator": numerator,
            "denominator": denominator,
            "value": round(numerator / denominator, 6) if denominator else None,
            "unit": "hours" if metric_id == "mttr_hours" else "ratio",
            "eligible_work_order_ids": eligible,
            "success_work_order_ids": successful if metric_id != "mttr_hours" else None,
            "warning": "NO_ELIGIBLE_RECORDS" if not denominator else None,
        })
    return results
