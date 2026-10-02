"""M5 fixture integrity and independent correctness checks."""

import json
from collections import Counter

from service_operations.evaluate import check_response, load_tasks
from service_operations.evaluation.reference import CsvReference
from service_operations.evaluation.tasks import AS_OF, DATA, DEFAULT_TASKS, build_tasks


def test_task_set_is_fixed_stratified_and_independent():
    tasks = build_tasks()
    assert len(tasks) == 100
    assert len({task["case_id"] for task in tasks}) == 100
    assert Counter(task["category"] for task in tasks) == {
        "metric": 60, "document": 20, "combined": 12, "refusal": 8,
    }
    assert Counter(task["split"] for task in tasks)["holdout"] == 20
    assert {task["category"] for task in tasks if task["split"] == "holdout"} == {
        "metric", "document", "combined", "refusal",
    }
    assert tasks == load_tasks(DEFAULT_TASKS, "all")
    assert all(task["review_status"] == "automated_reference_only" for task in tasks)


def test_independent_csv_oracle_matches_five_m0_reference_cases():
    oracle = CsvReference(DATA)
    cases = json.loads((DATA / "reference_cases.json").read_text(encoding="utf-8"))
    for case in cases:
        result = oracle.calculate(case["metric_id"], case["period"]["start"],
                                  case["period"]["end"], AS_OF, case["filters"])
        assert result["denominator"] == case["denominator"]
        assert result["value"] == case["value"]
        assert result["eligible_work_order_ids"] == sorted(case["eligible_work_order_ids"])
        assert result["success_work_order_ids"] == (sorted(case["success_work_order_ids"])
                                                    if case["success_work_order_ids"] is not None else None)
        assert abs(result["numerator"] - case["numerator"]) < 0.011


def test_evaluator_detects_wrong_metric_and_stale_version():
    tasks = load_tasks(DEFAULT_TASKS, "all")
    metric = next(task for task in tasks if task["category"] == "metric")
    bad_metric = {"metrics": [{**metric["expected"], "value": 0,
                               "eligible_work_order_ids": metric["expected"]["eligible_work_order_ids"],
                               "success_work_order_ids": metric["expected"]["success_work_order_ids"]}],
                  "citations": [], "warnings": []}
    assert "value_mismatch" in check_response(metric, bad_metric)
    document = next(task for task in tasks if task["category"] == "document"
                    and task["expected"]["citation"]["version"] == "v2")
    stale = {"metrics": [], "citations": [{**document["expected"]["citation"], "version": "v1"}],
             "warnings": []}
    assert "expected_citation_missing" in check_response(document, stale)
    assert "stale_version_cited" in check_response(document, stale)
