"""M3 checks for controlled routing, metric evidence and versioned citations."""

from pathlib import Path

import pytest

from service_operations.contracts import QueryPeriod, ServiceOpsRequest
from service_operations.manuals.cli import versions
from service_operations.manuals.search import ManualIndex
from service_operations.query_service import ServiceOpsQueryService
from service_operations.warehouse.importer import import_snapshot

SAMPLE = Path(__file__).resolve().parents[1] / "examples/m0"


@pytest.fixture
def service(tmp_path):
    db = tmp_path / "service.db"
    imported = import_snapshot(db, SAMPLE)
    assert imported.status == "imported"
    index = ManualIndex(tmp_path / "manual_index")
    for doc in versions():
        if doc.logical_doc_id == "demo_taz_pro_service_record_note":
            index.ingest(doc)
    return ServiceOpsQueryService(db, manual_index=index), imported.batch_id


def test_auto_combined_uses_sql_and_current_version(service):
    app, snapshot = service
    result = app.query({
        "question": "近三个月 NOZZLE_WIPE 重复故障率是否上升？应按哪个版本的材料记录流程处理？",
        "mode": "auto", "model": "TAZ_PRO", "fault_code": "NOZZLE_WIPE",
        "period": {"start": "2026-06-01", "end": "2026-09-01"},
        "as_of": "2026-09-28T00:00:00+08:00", "top_k": 1,
    })
    assert result["mode"] == "combined"
    assert result["warnings"] == []
    current = result["metrics"][0]
    assert (current["numerator"], current["denominator"]) == (25, 33)
    assert current["snapshot_id"] == snapshot
    prior = result["comparison"]["previous"]
    assert (prior["numerator"], prior["denominator"]) == (11, 21)
    assert prior["snapshot_id"] == snapshot
    assert result["comparison"]["delta_unit"] == "percentage_points"
    assert result["comparison"]["delta"] == pytest.approx(23.3766)
    assert {citation["version"] for citation in result["citations"]} == {"v2"}
    assert result["citations"][0]["page"] == 1
    assert "75.76%" in result["answer"]
    assert "演示便签不是厂商维修指引" in result["answer"]


def test_document_history_and_no_applicable_version(service):
    app, _ = service
    old = app.query(ServiceOpsRequest(
        question="NOZZLE_WIPE 服务记录流程", mode="document", model="TAZ_PRO",
        fault_code="NOZZLE_WIPE", as_of="2026-06-01T00:00:00+08:00", top_k=1,
    ))
    assert [item["version"] for item in old["citations"]] == ["v1"]
    assert old["metrics"] == []
    before = app.query(ServiceOpsRequest(
        question="NOZZLE_WIPE 服务记录流程", mode="document", model="TAZ_PRO",
        fault_code="NOZZLE_WIPE", as_of="2025-09-01T00:00:00+08:00",
    ))
    assert before["citations"] == []
    assert "NO_APPLICABLE_MANUAL" in before["warnings"]
    assert "维修步骤" not in before["answer"]


def test_metric_only_zero_denominator_and_no_document_lookup(service):
    app, _ = service
    result = app.query(ServiceOpsRequest(
        question="TAZ Pro 的平均修复时长是多少？", mode="metric", metric_id="mttr_hours",
        model="TAZ_PRO", period=QueryPeriod("2030-01-01", "2030-02-01"),
        as_of="2030-03-01T00:00:00+08:00",
    ))
    assert result["mode"] == "metric"
    assert result["metrics"][0]["value"] is None
    assert "NO_ELIGIBLE_WORK_ORDERS" in result["warnings"]
    assert result["citations"] == []


def test_combined_missing_metric_database_keeps_document_only(tmp_path):
    index = ManualIndex(tmp_path / "manual_index")
    for doc in versions():
        if doc.version == "v2" and doc.logical_doc_id == "demo_taz_pro_service_record_note":
            index.ingest(doc)
    app = ServiceOpsQueryService(tmp_path / "missing.db", manual_index=index)
    result = app.query(ServiceOpsRequest(
        question="NOZZLE_WIPE 重复故障率与当前记录流程", mode="combined",
        metric_id="repeat_fault_rate_30d", model="TAZ_PRO", fault_code="NOZZLE_WIPE",
        period=QueryPeriod("2026-06-01", "2026-09-01"),
        as_of="2026-09-28T00:00:00+08:00",
    ))
    assert result["metrics"] == []
    assert result["citations"][0]["version"] == "v2"
    assert "METRIC_UNAVAILABLE" in result["warnings"]
    assert "METRIC_EVIDENCE_MISSING" in result["warnings"]
    assert "75.76%" not in result["answer"]


def test_combined_missing_document_keeps_metric_only(service):
    app, _ = service
    result = app.query(ServiceOpsRequest(
        question="Workhorse 重复故障率及手册", mode="combined",
        metric_id="repeat_fault_rate_30d", model="TAZ_WORKHORSE",
        period=QueryPeriod("2026-06-01", "2026-09-01"),
        as_of="2026-09-28T00:00:00+08:00",
    ))
    assert result["metrics"]
    assert result["citations"] == []
    assert "NO_APPLICABLE_MANUAL" in result["warnings"]
    assert "DOCUMENT_EVIDENCE_MISSING" in result["warnings"]
    assert "适用资料" not in result["answer"]


def test_ambiguous_and_unknown_intents_abstain(service):
    app, _ = service
    ambiguous = app.query({"question": "比较 SLA 和 MTTR", "mode": "auto"})
    assert ambiguous["mode"] == "auto"
    assert ambiguous["metrics"] == []
    assert ambiguous["warnings"] == ["AMBIGUOUS_METRIC"]
    unknown = app.query({"question": "帮我看看情况", "mode": "auto"})
    assert unknown["warnings"] == ["UNRESOLVED_INTENT"]
    assert set(unknown["available_modes"]) == {"document", "metric", "combined"}


def test_time_and_filter_validation_prevent_future_or_arbitrary_sql(service):
    app, _ = service
    with pytest.raises(ValueError, match="Invalid model"):
        ServiceOpsRequest(question="MTTR", model="TAZ_PRO' OR 1=1 --")
    with pytest.raises(ValueError, match="timezone"):
        ServiceOpsRequest(question="手册", as_of="2026-09-28T00:00:00")
    future = app.query(ServiceOpsRequest(
        question="平均修复时长", mode="metric", metric_id="mttr_hours",
        period=QueryPeriod("2026-06-01", "2026-09-01"),
        as_of="2026-07-01T00:00:00+08:00",
    ))
    assert future["metrics"] == []
    assert future["warnings"] == ["PERIOD_AFTER_AS_OF"]


def test_previous_period_failure_preserves_current_metric(service, monkeypatch):
    app, _ = service
    import service_operations.query_service as module

    real_query = module.query_metric
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected previous-period failure")
        return real_query(*args, **kwargs)

    monkeypatch.setattr(module, "query_metric", fail_second)
    result = app.query(ServiceOpsRequest(
        question="NOZZLE_WIPE 重复故障率是否上升", mode="metric",
        metric_id="repeat_fault_rate_30d", model="TAZ_PRO", fault_code="NOZZLE_WIPE",
        period=QueryPeriod("2026-06-01", "2026-09-01"),
        as_of="2026-09-28T00:00:00+08:00",
    ))
    assert result["metrics"][0]["numerator"] == 25
    assert result["comparison"] is None
    assert result["warnings"] == ["COMPARISON_UNAVAILABLE"]
    assert "75.76%" in result["answer"]


def test_record_note_cannot_be_used_as_repair_instruction(service):
    app, _ = service
    result = app.query(ServiceOpsRequest(
        question="TAZ Pro 的 PROBE FAIL CLEAN NOZZLE 如何检查？", mode="document",
        model="TAZ_PRO", fault_code="NOZZLE_WIPE",
        as_of="2026-09-28T00:00:00+08:00",
    ))
    # The fixture deliberately indexes only the fictional note, not the manufacturer manual.
    assert result["citations"] == []
    assert "NO_RELEVANT_MANUAL_EVIDENCE" in result["warnings"]
    assert "repair" not in result["answer"].lower()


def test_record_and_repair_questions_choose_the_right_source(tmp_path):
    from service_operations.examples.manuals.build_demo_pdfs import build_pdf
    from service_operations.manuals.registry import ManualVersion
    from service_operations.manuals.search import file_hash

    index = ManualIndex(tmp_path / "manual_index")
    for doc in versions():
        if doc.logical_doc_id == "demo_taz_pro_service_record_note" and doc.version == "v2":
            index.ingest(doc)
    technical_pdf = tmp_path / "technical.pdf"
    technical_pdf.write_bytes(build_pdf([
        "PROBE FAIL CLEAN NOZZLE troubleshooting guidance.",
        "Use the correct Quickprint profile in Cura LulzBot Edition.",
    ]))
    index.ingest(ManualVersion(
        logical_doc_id="technical_manual_test", model="TAZ_PRO", version="v1",
        effective_from="2025-10-01T00:00:00+08:00", effective_to=None,
        source_path=str(technical_pdf), source_hash=file_hash(technical_pdf),
        title="Test technical manual", source_url="", license="Test fixture",
    ))
    app = ServiceOpsQueryService(tmp_path / "unused.db", manual_index=index)
    base = {"mode": "document", "model": "TAZ_PRO", "fault_code": "NOZZLE_WIPE",
            "as_of": "2026-09-28T00:00:00+08:00", "top_k": 1}
    record = app.query({**base, "question": "NOZZLE_WIPE 应按哪个版本记录流程？"})
    repair = app.query({**base, "question": "PROBE FAIL CLEAN NOZZLE 如何排查？"})
    assert record["citations"][0]["logical_doc_id"] == "demo_taz_pro_service_record_note"
    assert repair["citations"][0]["logical_doc_id"] == "technical_manual_test"
