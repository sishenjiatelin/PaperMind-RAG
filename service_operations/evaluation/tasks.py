"""Build a fixed, independently labelled M5 fixture from the frozen M0 CSVs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .reference import CsvReference

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples/m0"
DEFAULT_TASKS = ROOT / "examples/m5/tasks.jsonl"
AS_OF = "2026-09-28T00:00:00+08:00"
PRO = "lulzbot_taz_pro_user_manual"
WORKHORSE = "lulzbot_taz_workhorse_user_manual"
NOTE = "demo_taz_pro_service_record_note"
PERIODS = [("2025-10-01", "2025-12-01"), ("2025-12-01", "2026-02-01"),
           ("2026-02-01", "2026-04-01"), ("2026-04-01", "2026-06-01"),
           ("2026-06-01", "2026-09-01")]
LABELS = {"mttr_hours": "平均修复时长 MTTR", "sla_attainment": "SLA 达成率",
          "repeat_fault_rate_30d": "30 天重复故障率"}


def _case(kind: str, number: int, request: dict, expected: dict, *, source: str = "") -> dict:
    return {
        "case_id": f"{kind}-{number:03d}", "category": kind,
        "split": "holdout" if number % 5 == 0 or (kind == "refusal" and number == 8) else "development",
        "request": request, "expected": expected,
        "reference_source": source,
        "review_status": "automated_reference_only",
        "review_note": "Manual source/page labels need independent human review before portfolio claims.",
    }


def _metric_request(metric: str, model: str, period: tuple[str, str], **filters: str) -> dict:
    return {"question": f"{period[0]} 至 {period[1]} 的 {model} {LABELS[metric]}是多少？",
            "mode": "metric", "metric_id": metric, "model": model,
            "period": {"start": period[0], "end": period[1]}, "as_of": AS_OF,
            **filters}


def build_tasks(data_dir: Path = DATA) -> list[dict]:
    oracle = CsvReference(data_dir)
    tasks = []
    for metric in LABELS:
        for model in ("TAZ_PRO", "TAZ_WORKHORSE"):
            for site in ("EAST", "SOUTH"):
                for period in PERIODS:
                    request = _metric_request(metric, model, period, site=site)
                    filters = {"model": model, "site": site}
                    expected = oracle.calculate(metric, *period, AS_OF, filters)
                    expected.update({"metric_id": metric, "value_tolerance": 0.000001,
                                     "numerator_tolerance": 0.000001, "failure_type": "metric_mismatch"})
                    tasks.append(_case("metric", len(tasks) + 1, request, expected,
                                       source="M0 frozen CSVs; independent Python datetime calculation"))

    document_specs = []
    for model, source in (("TAZ_PRO", PRO), ("TAZ_WORKHORSE", WORKHORSE)):
        for term, page, variants in (
            ("PROBE FAIL CLEAN NOZZLE", 83, ["怎么处理", "如何排查", "处理步骤", "手册如何处置"]),
            ("MINTEMP", 82, ["怎么处理", "如何排查", "处理步骤", "手册如何处置"]),
        ):
            for wording in variants:
                document_specs.append((model, source, term, page, wording, AS_OF, None))
    for at, version in (("2026-06-15T00:00:00+08:00", "v1"),
                        ("2026-08-15T00:00:00+08:00", "v2")):
        for wording in ("服务记录流程是什么", "service record profile 记录流程是什么"):
            document_specs.append(("TAZ_PRO", NOTE, "NOZZLE_WIPE", 1, wording, at, version))
    for index, (model, source, term, page, wording, at, version) in enumerate(document_specs, 1):
        question = f"{model} 的 {term} {wording}？"
        request = {"question": question, "mode": "document", "model": model,
                   "as_of": at, "top_k": 3}
        if source == NOTE:
            request["fault_code"] = "NOZZLE_WIPE"
        expected = {"citation": {"logical_doc_id": source,
                                  "version": version or "demo-registry-v1", "page": page},
                    "failure_type": "citation_or_version_mismatch"}
        tasks.append(_case("document", index, request, expected,
                           source=f"PDF physical p.{page}; source manifest hash; version interval"))

    for index in range(1, 13):
        historical = index <= 6
        at = "2026-06-15T00:00:00+08:00" if historical else AS_OF
        period = ("2026-03-01", "2026-06-01") if historical else ("2026-06-01", "2026-09-01")
        model = "TAZ_PRO"
        site = ("EAST", "SOUTH", "WEST")[(index - 1) % 3]
        wording = ("服务记录流程", "service record profile")[(index - 1) // 3 % 2]
        request = {"question": f"{site} 的 TAZ Pro NOZZLE_WIPE 30 天重复故障率是多少？按哪个版本的{wording}处理？",
                   "mode": "combined", "metric_id": "repeat_fault_rate_30d", "model": model,
                   "site": site, "fault_code": "NOZZLE_WIPE", "period": {"start": period[0], "end": period[1]},
                   "as_of": at, "top_k": 3}
        expected = oracle.calculate("repeat_fault_rate_30d", *period, at,
                                    {"model": model, "site": site, "fault_code": "NOZZLE_WIPE"})
        expected.update({"metric_id": "repeat_fault_rate_30d", "value_tolerance": 0.000001,
                         "numerator_tolerance": 0.000001,
                         "citation": {"logical_doc_id": NOTE, "version": "v1" if historical else "v2", "page": 1},
                         "failure_type": "mixed_metric_or_citation_mismatch"})
        tasks.append(_case("combined", index, request, expected,
                           source="M0 CSV oracle + project-authored PDF p.1"))

    refusals = [
        ({"question": "这批数据为何变化？", "mode": "auto"}, "UNRESOLVED_INTENT"),
        ({"question": "TAZ Pro 手册在哪里？", "mode": "document"}, "MISSING_MODEL"),
        ({"question": "SLA 达成率是多少？", "mode": "metric", "metric_id": "sla_attainment"}, "MISSING_PERIOD"),
        ({"question": "平均修复时长是多少？", "mode": "metric", "metric_id": "mttr_hours",
          "period": {"start": "2026-10-01", "end": "2026-11-01"}, "as_of": AS_OF}, "PERIOD_AFTER_AS_OF"),
        ({"question": "TAZ Pro 平均修复时长是多少？", "mode": "metric", "metric_id": "mttr_hours",
          "model": "TAZ_PRO", "period": {"start": "2024-01-01", "end": "2024-02-01"},
          "as_of": AS_OF}, "NO_ELIGIBLE_WORK_ORDERS"),
        ({"question": "MTTR 与 SLA 都是多少？", "mode": "auto", "model": "TAZ_PRO",
          "period": {"start": "2026-01-01", "end": "2026-04-01"}, "as_of": AS_OF}, "AMBIGUOUS_METRIC"),
        ({"question": "TAZ Pro 维修手册怎么处理？", "mode": "document", "model": "TAZ_PRO",
          "as_of": "2024-01-01T00:00:00+08:00"}, "NO_APPLICABLE_MANUAL"),
        ({"question": "TAZ Pro 的 SLA 达成率和处理手册？", "mode": "combined",
          "metric_id": "sla_attainment", "period": {"start": "2026-01-01", "end": "2026-04-01"},
          "as_of": AS_OF}, "MISSING_MODEL"),
    ]
    for index, (request, warning) in enumerate(refusals, 1):
        tasks.append(_case("refusal", index, request,
                           {"warning": warning, "failure_type": "missing_refusal"},
                           source="M3 request and warning contract"))
    assert len(tasks) == 100 and sum(t["split"] == "holdout" for t in tasks) == 20
    return tasks


def write_tasks(path: Path = DEFAULT_TASKS, data_dir: Path = DATA) -> str:
    tasks = build_tasks(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n" for task in tasks)
    path.write_text(payload, encoding="utf-8")
    return hashlib.sha256(payload.encode()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_TASKS)
    args = parser.parse_args()
    print(json.dumps({"task_count": 100, "sha256": write_tasks(args.output)}, indent=2))
