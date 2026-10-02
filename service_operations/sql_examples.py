#!/usr/bin/env python3
"""Run the three M1 SQL examples against the active service snapshot."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from service_operations.warehouse.schema import connect

EXAMPLES = {
    "mttr": ("mttr_by_model.sql", "2026-04-01", "2026-07-01"),
    "sla": ("sla_by_priority.sql", "2026-04-01", "2026-07-01"),
    "repeat": ("repeat_by_fault.sql", "2026-06-01", "2026-09-01"),
}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def epoch_us(day: str) -> int:
    value = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    delta = value.astimezone(timezone.utc) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("example", choices=sorted(EXAMPLES))
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent / "runtime/service_operations.db")
    args = parser.parse_args()
    filename, start, end = EXAMPLES[args.example]
    sql = (Path(__file__).resolve().parent / "examples/sql" / filename).read_text(encoding="utf-8")
    connection = connect(args.db, read_only=True)
    try:
        params = {
            "start_us": epoch_us(start), "end_us": epoch_us(end),
            "as_of_us": epoch_us("2026-09-28"),
        }
        rows = [dict(row) for row in connection.execute(sql, params)]
    finally:
        connection.close()
    print(json.dumps({"example": args.example, "period": [start, end], "rows": rows}, indent=2))


if __name__ == "__main__":
    main()
