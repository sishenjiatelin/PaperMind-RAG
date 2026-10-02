#!/usr/bin/env python3
"""Import, inspect and query the M1 service operations SQLite snapshot.

Examples:
    python -m service_operations.cli import
    python -m service_operations.cli status
    python -m service_operations.cli metric --id mttr_hours --start 2026-01-01 --end 2026-04-01 --model TAZ_PRO
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from service_operations.metrics.query import METRIC_IDS, query_metric
from service_operations.warehouse.importer import (
    get_active_snapshot,
    get_batch_report,
    import_snapshot,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent / "runtime/service_operations.db")
    subparsers = parser.add_subparsers(dest="command", required=True)
    import_parser = subparsers.add_parser("import", help="Validate and import one full snapshot")
    import_parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent / "examples/m0")
    import_parser.add_argument("--work-orders", type=Path, help="Override work_orders.csv")
    import_parser.add_argument("--extra-work-orders", type=Path, help="Append rows before validation")
    subparsers.add_parser("status", help="Show the active snapshot")
    report_parser = subparsers.add_parser("report", help="Show a stored batch quality report")
    report_parser.add_argument("--batch-id", required=True)
    metric_parser = subparsers.add_parser("metric", help="Run one predefined SQL metric")
    metric_parser.add_argument("--id", choices=sorted(METRIC_IDS), required=True)
    metric_parser.add_argument("--start", required=True, help="Shanghai date or timezone-aware datetime")
    metric_parser.add_argument("--end", required=True, help="Exclusive bound")
    metric_parser.add_argument("--as-of", help="Timezone-aware datetime; default current UTC")
    metric_parser.add_argument("--snapshot-id")
    metric_parser.add_argument("--model")
    metric_parser.add_argument("--site")
    metric_parser.add_argument("--priority")
    metric_parser.add_argument("--fault-code")
    ask_parser = subparsers.add_parser("ask", help="Run an M3 document, metric or combined question")
    ask_parser.add_argument("--question", required=True)
    ask_parser.add_argument("--mode", choices=("auto", "document", "metric", "combined"), default="auto")
    ask_parser.add_argument("--metric-id", choices=sorted(METRIC_IDS))
    ask_parser.add_argument("--start", help="Shanghai date or timezone-aware datetime")
    ask_parser.add_argument("--end", help="Exclusive period bound")
    ask_parser.add_argument("--as-of", help="Timezone-aware datetime")
    ask_parser.add_argument("--snapshot-id")
    ask_parser.add_argument("--model")
    ask_parser.add_argument("--site")
    ask_parser.add_argument("--priority")
    ask_parser.add_argument("--fault-code")
    ask_parser.add_argument("--compare-previous", action="store_true", default=None)
    ask_parser.add_argument("--top-k", type=int, default=3)
    ask_parser.add_argument("--index-dir", type=Path, default=Path(__file__).resolve().parent / "runtime/manual_index")
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            result = import_snapshot(args.db, args.data_dir,
                                     work_orders_path=args.work_orders,
                                     extra_work_orders_path=args.extra_work_orders)
            output = result.to_dict()
            code = 2 if result.status == "rejected" else 0
        elif args.command == "status":
            output = get_active_snapshot(args.db)
            code = 0
        elif args.command == "report":
            output = get_batch_report(args.db, args.batch_id)
            if output is None:
                raise ValueError(f"Unknown batch: {args.batch_id}")
            code = 0
        elif args.command == "ask":
            from service_operations.contracts import QueryPeriod, ServiceOpsRequest
            from service_operations.query_service import ServiceOpsQueryService

            if (args.start is None) != (args.end is None):
                raise ValueError("Provide both --start and --end for a metric period")
            request = ServiceOpsRequest(
                question=args.question, mode=args.mode, metric_id=args.metric_id,
                model=args.model, site=args.site, priority=args.priority,
                fault_code=args.fault_code, as_of=args.as_of,
                snapshot_id=args.snapshot_id, compare_previous=args.compare_previous,
                top_k=args.top_k,
                period=QueryPeriod(args.start, args.end) if args.start else None,
            )
            output = ServiceOpsQueryService(args.db, manual_index_dir=args.index_dir).query(request)
            code = 0
        else:
            output = query_metric(
                args.db, args.id, args.start, args.end,
                as_of=args.as_of, snapshot_id=args.snapshot_id,
                model=args.model, site=args.site, priority=args.priority,
                fault_code=args.fault_code,
            )
            code = 0
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return code
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
