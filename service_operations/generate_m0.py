"""Generate the M0 synthetic service operations CSV fixture with only stdlib."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from service_operations.m0_reference import build_reference

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
DEFAULT_CONFIG = PACKAGE_ROOT / "config/m0.json"
DEFAULT_OUTPUT = PACKAGE_ROOT / "examples/m0"
ASSET_FIELDS = ["asset_id", "model", "site"]
ORDER_FIELDS = [
    "work_order_id", "asset_id", "fault_code", "priority",
    "opened_at", "resolved_at", "status",
]
POLICY_FIELDS = [
    "policy_id", "priority", "effective_from", "effective_to", "resolution_hours",
]


def utc_string(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def choose(rng: random.Random, values: list[str], weights: list[float]) -> str:
    return rng.choices(values, weights=weights, k=1)[0]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(config: dict[str, object], output: Path, config_path: Path) -> dict[str, object]:
    """Return an immutable manifest for the generated, clearly synthetic fixture."""
    output.mkdir(parents=True, exist_ok=True)
    rng = random.Random(config["seed"])
    tz = ZoneInfo(str(config["timezone"]))
    models = list(config["models"])
    sites = list(config["sites"])
    faults = list(config["fault_codes"])
    priorities = list(config["priority_weights"])
    priority_weights = list(config["priority_weights"].values())

    assets: list[dict[str, object]] = []
    for model in models:
        for site in sites:
            for number in range(1, int(config["assets_per_model_site"]) + 1):
                assets.append({
                    "asset_id": f"{model}-{site}-{number:02d}",
                    "model": model,
                    "site": site,
                })
    by_id = {str(asset["asset_id"]): asset for asset in assets}

    start = date.fromisoformat(str(config["start_date"]))
    end = date.fromisoformat(str(config["end_date_exclusive"]))
    trend = config["trend"]
    south_delay = config["south_delay"]
    orders: list[dict[str, object]] = []
    resolved_history: list[tuple[datetime, str, str]] = []
    current = start
    while current < end:
        opened_times = sorted(
            datetime.combine(
                current,
                time(hour=rng.randrange(8, 19), minute=rng.randrange(60)),
                tzinfo=tz,
            )
            for _ in range(int(config["tickets_per_day"]))
        )
        for opened_at in opened_times:
            candidates = [
                (asset_id, fault)
                for resolved_at, asset_id, fault in resolved_history
                if opened_at - timedelta(hours=720) <= resolved_at < opened_at
            ]
            repeat_attempt = rng.random() < float(config["repeat_attempt_probability"])
            if repeat_attempt and candidates:
                asset_id, fault = rng.choice(candidates)
                asset = by_id[asset_id]
            else:
                asset = rng.choice(assets)
                asset_id = str(asset["asset_id"])
                weights = list(config["fault_weights"][asset["model"]])
                if asset["model"] == trend["model"] and current >= date.fromisoformat(trend["from_date"]):
                    weights[faults.index(trend["fault_code"])] = trend["weight_after"]
                fault = choose(rng, faults, weights)

            priority = choose(rng, priorities, priority_weights)
            open_window_start = end - timedelta(days=int(config["open_window_days"]))
            status = "open" if current >= open_window_start and rng.random() < float(config["open_probability"]) else "resolved"
            resolved_at = None
            if status == "resolved":
                median = float(config["median_resolution_hours"][priority])
                duration = rng.lognormvariate(math.log(median), float(config["resolution_log_sigma"]))
                if asset["site"] == "SOUTH" and current >= date.fromisoformat(south_delay["from_date"]):
                    duration *= float(south_delay["factor"])
                duration = min(max(duration, float(config["min_resolution_hours"])),
                               float(config["max_resolution_hours"]))
                resolved_at = opened_at + timedelta(hours=round(duration, 2))
                resolved_history.append((resolved_at, asset_id, fault))

            orders.append({
                "work_order_id": f"WO-{len(orders) + 1:05d}",
                "asset_id": asset_id,
                "fault_code": fault,
                "priority": priority,
                "opened_at": utc_string(opened_at),
                "resolved_at": utc_string(resolved_at) if resolved_at else "",
                "status": status,
            })
        current += timedelta(days=1)

    policies = []
    for policy in config["sla_policies"]:
        policies.append({
            **policy,
            "effective_from": utc_string(datetime.fromisoformat(policy["effective_from"])),
            "effective_to": utc_string(datetime.fromisoformat(policy["effective_to"]))
            if policy["effective_to"] else "",
        })

    invalid = []
    invalid_cases = []

    def add_invalid(rule_id: str, **changes: str) -> None:
        row = dict(orders[0])
        row["work_order_id"] = f"INVALID-{len(invalid) + 1:02d}"
        row.update(changes)
        invalid.append(row)
        invalid_cases.append({"csv_row_number": len(invalid) + 1, "expected_rule_id": rule_id})

    add_invalid("DUPLICATE_WORK_ORDER_ID", work_order_id=str(orders[0]["work_order_id"]))
    add_invalid("UNKNOWN_ASSET", asset_id="NOT-IN-ASSETS")
    add_invalid("INVALID_PRIORITY", priority="P9")
    add_invalid("RESOLVED_WITHOUT_TIMESTAMP", resolved_at="")
    add_invalid("RESOLVED_BEFORE_OPENED", resolved_at="2025-01-01T00:00:00Z")
    add_invalid("INVALID_TIMESTAMP", opened_at="not-a-timestamp")

    files = {
        "assets.csv": (ASSET_FIELDS, assets),
        "work_orders.csv": (ORDER_FIELDS, orders),
        "sla_policies.csv": (POLICY_FIELDS, policies),
        "invalid/invalid_work_orders.csv": (ORDER_FIELDS, invalid),
    }
    manifest_files = {}
    for name, (fields, rows) in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_csv(path, fields, rows)
        manifest_files[name] = {"rows": len(rows), "sha256": sha256(path)}
    write_json(output / "invalid/invalid_cases.json", invalid_cases)
    manifest_files["invalid/invalid_cases.json"] = {
        "rows": len(invalid_cases), "sha256": sha256(output / "invalid/invalid_cases.json")
    }

    reference = build_reference(orders, by_id, policies, config)
    write_json(output / "reference_cases.json", reference)
    manifest_files["reference_cases.json"] = {
        "rows": len(reference), "sha256": sha256(output / "reference_cases.json")
    }

    manifest = {
        "synthetic": True,
        "scenario": "fictional additive manufacturing service fleet",
        "schema_version": config["schema_version"],
        "generator_version": config["generator_version"],
        "seed": config["seed"],
        "config_path": str(config_path.relative_to(REPO_ROOT)) if config_path.is_relative_to(REPO_ROOT) else str(config_path),
        "config_sha256": sha256(config_path),
        "manual_source_registry": "service_operations/docs/manual_sources.json",
        "start_date": config["start_date"],
        "end_date_exclusive": config["end_date_exclusive"],
        "as_of": config["as_of"],
        "files": manifest_files,
    }
    write_json(output / "sample_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    manifest = generate(config, args.output, args.config.resolve())
    print(f"Generated synthetic M0 fixture: {manifest['files']['work_orders.csv']['rows']} work orders in {args.output}")


if __name__ == "__main__":
    main()
