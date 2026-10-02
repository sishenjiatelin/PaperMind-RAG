"""M4 dashboard read model and headless page checks."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from service_operations.contracts import QueryPeriod
from service_operations.dashboard import _chart_row_from_selection
from service_operations.dashboard_data import ServiceOpsDashboardData
from service_operations.manuals.cli import versions
from service_operations.manuals.search import ManualIndex
from service_operations.warehouse.importer import import_snapshot

SAMPLE = Path(__file__).resolve().parents[1] / "examples/m0"
DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard.py"
AS_OF = "2026-09-28T00:00:00+08:00"


@pytest.fixture
def data(tmp_path):
    db = tmp_path / "service.db"
    imported = import_snapshot(db, SAMPLE)
    assert imported.status == "imported"
    return ServiceOpsDashboardData(db, tmp_path / "manual_index"), imported.batch_id


def test_missing_db_and_snapshot_status(tmp_path):
    view = ServiceOpsDashboardData(tmp_path / "missing.db", tmp_path / "missing_index")
    assert view.status()["reason"] == "SERVICE_DB_MISSING"
    assert view.batches() == []
    assert view.manual_versions() == []


def test_status_trend_and_drilldown_share_one_snapshot(data):
    view, snapshot = data
    status = view.status()
    assert status["snapshot_id"] == snapshot
    assert status["row_counts"] == {"assets": 18, "work_orders": 670, "sla_policies": 4}
    assert status["default_period"] == {"start": "2026-06-01", "end": "2026-09-01"}
    rows = view.trend("repeat_fault_rate_30d", QueryPeriod("2026-06-01", "2026-09-01"),
                      as_of=AS_OF, snapshot_id=snapshot, group_by="fault_code", model="TAZ_PRO")
    nozzle = [row for row in rows if row["group"] == "NOZZLE_WIPE"]
    assert len(nozzle) == 3
    assert sum(row["numerator"] for row in nozzle) == 25
    assert sum(row["denominator"] for row in nozzle) == 33
    assert all(row["metric"]["snapshot_id"] == snapshot for row in nozzle)
    august = nozzle[-1]["metric"]
    details = view.metric_details(august, limit=5)
    assert details["total"] == august["denominator"] == 9
    assert len(details["rows"]) == 5
    assert {row["work_order_id"] for row in details["rows"]} <= set(august["eligible_work_order_ids"])
    assert all(row["model"] == "TAZ_PRO" and row["fault_code"] == "NOZZLE_WIPE"
               for row in details["rows"])
    with pytest.raises(ValueError, match="Unknown group_by"):
        view.trend("mttr_hours", QueryPeriod("2026-06-01", "2026-09-01"),
                   as_of=AS_OF, snapshot_id=snapshot, group_by="arbitrary_sql")


def test_rejected_demo_batch_visible_without_changing_snapshot(data):
    view, snapshot = data
    rejected = import_snapshot(
        view.db_path, SAMPLE,
        extra_work_orders_path=SAMPLE / "invalid/invalid_work_orders.csv",
    )
    assert rejected.status == "rejected"
    status = view.status()
    assert status["snapshot_id"] == snapshot
    assert status["quality_issue_count"] == 6
    assert status["batch_counts"] == {"imported": 1, "rejected": 1}
    assert view.batches()[0]["batch_id"] == rejected.batch_id
    report = view.batch_report(rejected.batch_id)
    assert report["issue_count"] == 6
    assert len(report["issues"]) == 6


def test_manual_version_health_and_effective_date(data):
    view, _ = data
    index = ManualIndex(view.manual_index_dir)
    for doc in versions():
        if doc.logical_doc_id == "demo_taz_pro_service_record_note":
            index.ingest(doc)
    rows = view.manual_versions()
    assert len(rows) == 2
    assert all(row["index_ready"] and row["chroma_count"] == 1 for row in rows)
    assert {(row["version"], row["current"]) for row in rows} == {("v1", False), ("v2", True)}


def test_chart_selection_resolves_exact_month_and_group():
    rows = [{"period_start": "2026-06-01T00:00:00+08:00", "group": "NOZZLE_WIPE"},
            {"period_start": "2026-07-01T00:00:00+08:00", "group": "NOZZLE_WIPE"}]
    event = SimpleNamespace(selection={"pick": [rows[1]]})
    assert _chart_row_from_selection(event, rows) is rows[1]
    assert _chart_row_from_selection(SimpleNamespace(selection={}), rows) is None


def test_page_renders_headlessly_without_browser():
    page = AppTest.from_file(str(DASHBOARD), default_timeout=30).run()
    assert not page.exception
    assert page.title[0].value == "设备售后服务运营"
    assert len(page.tabs) == 3


def test_page_demo_buttons_and_combined_question(tmp_path):
    index_dir = tmp_path / "manual_index"
    index = ManualIndex(index_dir)
    for doc in versions():
        if doc.logical_doc_id == "demo_taz_pro_service_record_note":
            index.ingest(doc)
    db = tmp_path / "service.db"
    wrapper = tmp_path / "page.py"
    wrapper.write_text(
        "from service_operations.dashboard import render\n"
        f"render(db_path={str(db)!r}, manual_index_dir={str(index_dir)!r})\n"
    )
    page = AppTest.from_file(str(wrapper), default_timeout=30).run()
    next(button for button in page.button if button.label == "导入 M0 合成样本").click().run()
    assert not page.exception
    assert next(item.value for item in page.metric if item.label == "工单") == "670"
    next(button for button in page.button if button.label == "查询").click().run()
    assert not page.exception
    assert any("75.76%" in item.value and "Demo service record note v2" in item.value
               for item in page.markdown)
    next(button for button in page.button if button.label == "导入故意错误批次").click().run()
    assert not page.exception
    assert next(item.value for item in page.metric if item.label == "工单") == "670"
    assert next(item.value for item in page.metric if item.label == "质量问题记录") == "6"
