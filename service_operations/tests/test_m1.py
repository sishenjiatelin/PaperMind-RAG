"""M1 end-to-end checks against M0's independent Python reference cases."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from service_operations.metrics.query import query_metric
from service_operations.warehouse.importer import (
    get_active_snapshot,
    get_batch_report,
    import_snapshot,
)

SAMPLE = Path(__file__).resolve().parents[1] / "examples/m0"


@pytest.fixture
def imported_db(tmp_path: Path) -> tuple[Path, str]:
    db = tmp_path / "service.db"
    result = import_snapshot(db, SAMPLE)
    assert result.status == "imported"
    return db, result.batch_id


def test_reference_cases_match_sql(imported_db: tuple[Path, str]) -> None:
    db, snapshot_id = imported_db
    cases = json.loads((SAMPLE / "reference_cases.json").read_text())
    for case in cases:
        actual = query_metric(
            db, case["metric_id"], case["period"]["start"], case["period"]["end"],
            as_of=case["as_of"], snapshot_id=snapshot_id, **case["filters"],
        )
        assert actual["denominator"] == case["denominator"], case["case_id"]
        assert actual["numerator"] == pytest.approx(case["numerator"]), case["case_id"]
        assert actual["value"] == pytest.approx(case["value"], abs=0.000001), case["case_id"]
        assert set(actual["eligible_work_order_ids"]) == set(case["eligible_work_order_ids"])
        if case["success_work_order_ids"] is not None:
            assert set(actual["success_work_order_ids"]) == set(case["success_work_order_ids"])
        if case["metric_id"] == "repeat_fault_rate_30d":
            assert set(actual["prior_work_order_ids"]) == set(case["success_work_order_ids"])


def test_idempotent_import_and_rejected_batch_preserve_active(imported_db: tuple[Path, str]) -> None:
    db, snapshot_id = imported_db
    duplicate = import_snapshot(db, SAMPLE)
    assert duplicate.status == "noop"
    assert duplicate.batch_id == snapshot_id
    bad = import_snapshot(
        db, SAMPLE, extra_work_orders_path=SAMPLE / "invalid/invalid_work_orders.csv"
    )
    assert bad.status == "rejected"
    assert bad.snapshot_id == snapshot_id
    assert {issue.rule_id for issue in bad.issues} == {
        "DUPLICATE_WORK_ORDER_ID", "UNKNOWN_ASSET", "INVALID_PRIORITY",
        "RESOLVED_WITHOUT_TIMESTAMP", "RESOLVED_BEFORE_OPENED", "INVALID_TIMESTAMP",
    }
    report = get_batch_report(db, bad.batch_id)
    assert report["issue_count"] == 6
    assert report["row_counts"]["extra_work_orders.csv"] == {
        "total": 6, "valid": 0, "rejected": 6, "imported": 0,
    }
    assert get_active_snapshot(db)["snapshot_id"] == snapshot_id
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0] == 670
        assert connection.execute("SELECT COUNT(*) FROM ingestion_batches").fetchone()[0] == 2


def test_database_failure_rolls_back_complete_snapshot(imported_db: tuple[Path, str], tmp_path: Path) -> None:
    db, snapshot_id = imported_db
    folder = tmp_path / "changed"
    folder.mkdir()
    for name in ("assets.csv", "sla_policies.csv", "work_orders.csv"):
        (folder / name).write_bytes((SAMPLE / name).read_bytes())
    path = folder / "work_orders.csv"
    content = path.read_text().replace("WO-00001", "WO-RENAMED", 1)
    path.write_text(content)
    with sqlite3.connect(db) as connection:
        connection.execute("""
            CREATE TRIGGER fail_new_order BEFORE INSERT ON work_orders
            WHEN NEW.work_order_id = 'WO-RENAMED'
            BEGIN SELECT RAISE(ABORT, 'injected failure'); END
        """)
    with pytest.raises(sqlite3.IntegrityError):
        import_snapshot(db, folder)
    assert get_active_snapshot(db)["snapshot_id"] == snapshot_id
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM ingestion_batches").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0] == 670


def test_policy_overlap_and_missing_policy_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "input"
    folder.mkdir()
    for name in ("assets.csv", "sla_policies.csv", "work_orders.csv"):
        (folder / name).write_bytes((SAMPLE / name).read_bytes())
    policy_path = folder / "sla_policies.csv"
    with policy_path.open(newline="") as file:
        rows = list(csv.DictReader(file))
    rows.append({**rows[1], "policy_id": "P2-OVERLAP"})
    with policy_path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    overlap = import_snapshot(tmp_path / "overlap.db", folder)
    assert overlap.status == "rejected"
    assert "OVERLAPPING_SLA_POLICY" in {issue.rule_id for issue in overlap.issues}
    assert "AMBIGUOUS_SLA_POLICY" in {issue.rule_id for issue in overlap.issues}
    assert get_active_snapshot(tmp_path / "overlap.db") is None
    rows = [row for row in rows if row["priority"] != "P1"]
    with policy_path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    missing = import_snapshot(tmp_path / "missing.db", folder)
    assert "MISSING_SLA_POLICY" in {issue.rule_id for issue in missing.issues}


def test_repeat_boundary_as_of_and_empty_result(tmp_path: Path) -> None:
    folder = tmp_path / "edge"
    folder.mkdir()
    (folder / "assets.csv").write_text("asset_id,model,site\nA,TAZ_PRO,EAST\n")
    (folder / "sla_policies.csv").write_text(
        "policy_id,priority,effective_from,effective_to,resolution_hours\n"
        "P,P1,2025-01-01T00:00:00Z,,24\n"
    )
    (folder / "work_orders.csv").write_text(
        "work_order_id,asset_id,fault_code,priority,opened_at,resolved_at,status\n"
        "A1,A,HOTEND_TEMP,P1,2026-01-01T00:00:00Z,2026-01-01T12:00:00Z,resolved\n"
        "B1,A,HOTEND_TEMP,P1,2026-01-31T12:00:00Z,,open\n"
        "C1,A,HOTEND_TEMP,P1,2026-02-01T12:00:00Z,,open\n"
    )
    db = tmp_path / "edge.db"
    assert import_snapshot(db, folder).status == "imported"
    repeat = query_metric(db, "repeat_fault_rate_30d", "2026-01-31T00:00:00Z",
                          "2026-02-02T00:00:00Z", as_of="2026-02-10T00:00:00Z")
    assert repeat["numerator"] == 1 and repeat["denominator"] == 2
    assert repeat["prior_work_order_ids"] == {"B1": "A1"}
    before_due = query_metric(db, "sla_attainment", "2026-01-31T00:00:00Z",
                              "2026-02-02T12:00:01Z", as_of="2026-02-01T11:59:59Z")
    assert before_due["denominator"] == 0 and before_due["value"] is None
    at_due = query_metric(db, "sla_attainment", "2026-01-31T00:00:00Z",
                          "2026-02-02T12:00:01Z", as_of="2026-02-01T12:00:00Z")
    assert at_due["denominator"] == 1 and at_due["numerator"] == 0
    empty = query_metric(db, "mttr_hours", "2026-03-01", "2026-04-01")
    assert empty["value"] is None and empty["warning"] == "NO_ELIGIBLE_RECORDS"
    with pytest.raises(ValueError, match="timezone"):
        query_metric(db, "mttr_hours", "2026-01-01T00:00:00", "2026-02-01T00:00:00Z")
    with pytest.raises(ValueError, match="Invalid model"):
        query_metric(db, "mttr_hours", "2026-01-01", "2026-02-01",
                     model="TAZ_PRO' OR 1=1 --")


def test_new_valid_snapshot_switches_active_and_old_snapshot_remains_queryable(
    imported_db: tuple[Path, str], tmp_path: Path,
) -> None:
    db, first_id = imported_db
    folder = tmp_path / "next"
    folder.mkdir()
    for name in ("assets.csv", "sla_policies.csv", "work_orders.csv"):
        (folder / name).write_bytes((SAMPLE / name).read_bytes())
    orders = folder / "work_orders.csv"
    orders.write_text(orders.read_text().replace("WO-00001", "WO-NEW-01", 1))
    second = import_snapshot(db, folder)
    assert second.status == "imported" and second.batch_id != first_id
    assert get_active_snapshot(db)["snapshot_id"] == second.batch_id
    old = query_metric(db, "mttr_hours", "2025-10-01", "2025-10-03",
                       snapshot_id=first_id)
    new = query_metric(db, "mttr_hours", "2025-10-01", "2025-10-03")
    assert "WO-00001" in old["eligible_work_order_ids"]
    assert "WO-NEW-01" in new["eligible_work_order_ids"]
    assert old["value"] == new["value"]


def test_malformed_csv_width_is_counted_and_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "bad_width"
    folder.mkdir()
    for name in ("assets.csv", "sla_policies.csv", "work_orders.csv"):
        (folder / name).write_bytes((SAMPLE / name).read_bytes())
    with (folder / "work_orders.csv").open("a") as file:
        file.write("too,many,fields,in,this,CSV,row,extra\n")
    db = tmp_path / "bad_width.db"
    result = import_snapshot(db, folder)
    assert result.status == "rejected"
    assert "INVALID_ROW_WIDTH" in {issue.rule_id for issue in result.issues}
    assert result.row_counts["work_orders.csv"] == {
        "total": 671, "valid": 670, "rejected": 1, "imported": 0,
    }
    assert get_active_snapshot(db) is None


def test_policy_effective_boundary_uses_policy_at_opened_time(tmp_path: Path) -> None:
    folder = tmp_path / "policy_edge"
    folder.mkdir()
    (folder / "assets.csv").write_text("asset_id,model,site\nA,TAZ_PRO,EAST\n")
    (folder / "sla_policies.csv").write_text(
        "policy_id,priority,effective_from,effective_to,resolution_hours\n"
        "OLD,P2,2026-01-01T00:00:00+08:00,2026-04-01T00:00:00+08:00,72\n"
        "NEW,P2,2026-04-01T00:00:00+08:00,,48\n"
    )
    (folder / "work_orders.csv").write_text(
        "work_order_id,asset_id,fault_code,priority,opened_at,resolved_at,status\n"
        "BEFORE,A,HOTEND_TEMP,P2,2026-03-31T23:59:59+08:00,,open\n"
        "AT,A,HOTEND_TEMP,P2,2026-04-01T00:00:00+08:00,,open\n"
    )
    db = tmp_path / "policy_edge.db"
    assert import_snapshot(db, folder).status == "imported"
    with sqlite3.connect(db) as connection:
        rows = dict(connection.execute(
            "SELECT work_order_id, policy_id FROM work_orders"
        ).fetchall())
    assert rows == {"BEFORE": "OLD", "AT": "NEW"}


def test_invalid_header_does_not_report_unchecked_rows_as_valid(tmp_path: Path) -> None:
    folder = tmp_path / "bad_header"
    folder.mkdir()
    for name in ("assets.csv", "sla_policies.csv", "work_orders.csv"):
        (folder / name).write_bytes((SAMPLE / name).read_bytes())
    orders = folder / "work_orders.csv"
    orders.write_text(orders.read_text().replace("work_order_id,", "wrong_id,", 1))
    result = import_snapshot(tmp_path / "bad_header.db", folder)
    assert result.status == "rejected"
    assert result.row_counts["work_orders.csv"] == {
        "total": 670, "valid": 0, "rejected": 670, "imported": 0,
    }
    assert result.to_dict()["rule_counts"] == {"INVALID_HEADER": 1}
