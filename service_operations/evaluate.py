"""Run the frozen M5 business tasks and write a reproducible JSON failure report."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from service_operations.evaluation.tasks import DEFAULT_TASKS, ROOT
from service_operations.manuals.search import ManualIndex
from service_operations.query_service import (
    DEFAULT_DB,
    DEFAULT_MANUAL_INDEX,
    ServiceOpsQueryService,
)
from service_operations.warehouse.schema import connect

DEFAULT_OUTPUT = ROOT / "runtime/evaluation"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_tasks(path: Path, split: str) -> list[dict]:
    tasks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [task["case_id"] for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case_id in task file")
    if split == "all":
        return tasks
    return [task for task in tasks if task["split"] == split]


def _metric_errors(actual: dict | None, expected: dict) -> list[str]:
    if actual is None:
        return ["metric_missing"]
    failures = []
    for key in ("metric_id", "denominator"):
        if actual[key] != expected[key]:
            failures.append(f"{key}_mismatch")
    for key, tolerance in (("value", expected["value_tolerance"]),
                           ("numerator", expected["numerator_tolerance"])):
        left, right = actual[key], expected[key]
        if (left is None) != (right is None) or (left is not None and abs(left - right) > tolerance):
            failures.append(f"{key}_mismatch")
    if sorted(actual["eligible_work_order_ids"]) != expected["eligible_work_order_ids"]:
        failures.append("eligible_ids_mismatch")
    if actual["success_work_order_ids"] is not None and sorted(actual["success_work_order_ids"]) != expected["success_work_order_ids"]:
        failures.append("success_ids_mismatch")
    return failures


def _citation_match(citation: dict, expected: dict) -> bool:
    return all(citation.get(key) == expected[key] for key in ("logical_doc_id", "version", "page"))


def check_response(task: dict, response: dict) -> list[str]:
    expected = task["expected"]
    errors = []
    if task["category"] in {"metric", "combined"}:
        errors += _metric_errors(response["metrics"][0] if response["metrics"] else None, expected)
    if task["category"] in {"document", "combined"}:
        target = expected["citation"]
        if not any(_citation_match(item, target) for item in response["citations"]):
            errors.append("expected_citation_missing")
        if task["request"].get("fault_code") == "NOZZLE_WIPE":
            if any(item["logical_doc_id"] == target["logical_doc_id"] and item["version"] != target["version"]
                   for item in response["citations"]):
                errors.append("stale_version_cited")
        if not response["citations"] and "DOCUMENT_EVIDENCE_MISSING" not in response["warnings"] and task["category"] == "combined":
            errors.append("missing_document_warning")
    if task["category"] == "refusal":
        if expected["warning"] not in response["warnings"]:
            errors.append("expected_warning_missing")
        if expected["warning"] != "NO_ELIGIBLE_WORK_ORDERS" and response["metrics"]:
            errors.append("unexpected_metric")
    return errors


def _retrieval_query(request: dict) -> str:
    query = request["question"]
    if request.get("fault_code"):
        query += " " + request["fault_code"] + " PROBE FAIL CLEAN NOZZLE"
    return query


def hybrid_rank(index: ManualIndex, request: dict, expected: dict) -> int | None:
    hits = index.search(_retrieval_query(request), request["model"],
                        as_of=request["as_of"], fault_code=request.get("fault_code"), top_k=3)
    for rank, hit in enumerate(hits, 1):
        if _citation_match(hit, expected):
            return rank
    return None


def bm25_baseline(index: ManualIndex, request: dict, expected: dict) -> int | None:
    """Rank chunks using only BM25, with the same published-version gate."""
    terms = index.encoder._tokenize(_retrieval_query(request))
    candidates = []
    for doc in index.registry.select(request["model"], request["as_of"], request.get("fault_code")):
        engine = index._indexer()
        if not engine.load(doc.namespace):
            raise RuntimeError(f"Missing baseline BM25 index: {doc.namespace}")
        for hit in engine.query(terms, top_k=20):
            candidates.append((hit["score"], doc, hit["chunk_id"]))
    candidates.sort(key=lambda item: (-item[0], item[2]))
    for rank, (_, doc, chunk_id) in enumerate(candidates[:3], 1):
        metadata = index.chroma.get_collection(doc.namespace).get(ids=[chunk_id], include=["metadatas"])["metadatas"][0]
        if doc.logical_doc_id == expected["logical_doc_id"] and doc.version == expected["version"] and metadata["page"] == expected["page"]:
            return rank
    return None


def _snapshot(db_path: Path) -> dict:
    connection = connect(db_path, read_only=True)
    try:
        row = connection.execute("SELECT a.snapshot_id, b.source_hash, b.imported_at, b.row_counts_json FROM active_snapshot a JOIN ingestion_batches b ON b.batch_id=a.snapshot_id").fetchone()
        if row is None:
            raise ValueError("No active data snapshot")
        return {"snapshot_id": row["snapshot_id"], "source_hash": row["source_hash"],
                "imported_at": row["imported_at"], "row_counts": json.loads(row["row_counts_json"])}
    finally:
        connection.close()


def evaluate(task_path: Path = DEFAULT_TASKS, db_path: Path = DEFAULT_DB,
             index_dir: Path = DEFAULT_MANUAL_INDEX, split: str = "all") -> dict:
    tasks = load_tasks(task_path, split)
    index = ManualIndex(index_dir)
    service = ServiceOpsQueryService(db_path, index)
    results = []
    for task in tasks:
        started = time.perf_counter()
        try:
            response = service.query(task["request"])
            errors = check_response(task, response)
            actual = {"warnings": response["warnings"],
                      "metric": {key: response["metrics"][0].get(key) for key in ("value", "numerator", "denominator", "snapshot_id")}
                      if response["metrics"] else None,
                      "citations": [{key: item[key] for key in ("logical_doc_id", "version", "page", "source_hash")}
                                    for item in response["citations"]]}
        except Exception as exc:
            errors = ["execution_error"]
            actual = {"error": f"{type(exc).__name__}: {exc}"}
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        baseline_rank = hybrid_rank_at_3 = None
        if task["category"] == "document":
            try:
                hybrid_rank_at_3 = hybrid_rank(index, task["request"], task["expected"]["citation"])
                baseline_rank = bm25_baseline(index, task["request"], task["expected"]["citation"])
            except (RuntimeError, sqlite3.Error, OSError) as exc:
                errors.append("baseline_error")
                actual["baseline_error"] = f"{type(exc).__name__}: {exc}"
        results.append({"case_id": task["case_id"], "category": task["category"],
                        "split": task["split"], "passed": not errors, "errors": errors,
                        "expected": task["expected"], "actual": actual,
                        "latency_ms": elapsed_ms, "hybrid_rank_at_3": hybrid_rank_at_3,
                        "bm25_rank_at_3": baseline_rank})
    groups = {}
    for category in sorted({task["category"] for task in tasks}):
        subset = [item for item in results if item["category"] == category]
        groups[category] = {"cases": len(subset), "passed": sum(item["passed"] for item in subset),
                            "failed": sum(not item["passed"] for item in subset)}
    docs = [item for item in results if item["category"] == "document"]
    registry = [{"logical_doc_id": doc.logical_doc_id, "version": doc.version,
                 "source_hash": doc.source_hash, "chunk_count": doc.chunk_count}
                for doc in index.registry.list() if doc.publication_status == "published"]
    return {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "task_file": str(task_path),
            "task_sha256": sha256(task_path), "split": split, "data_snapshot": _snapshot(db_path),
            "manual_registry": registry,
            "retrieval_config": {"hybrid": "hash-vector cosine + BM25, RRF k=60, lexical rerank; top_k=3",
                                 "baseline": "BM25 only; same model/effective-version gate; top_k=3",
                                 "embedding": "deterministic 384-dimensional lexical hash; no hosted model"},
            "environment": {"python": sys.version.split()[0], "platform": platform.platform(),
                            "sqlite": sqlite3.sqlite_version, "latency_note": "Local sequential warm-index wall time; no concurrency or production QPS inference"},
            "review_status": "automatic checks only; PDF source/page labels and answer wording require human review",
            "summary": {"cases": len(results), "passed": sum(item["passed"] for item in results),
                        "failed": sum(not item["passed"] for item in results), "by_category": groups,
                        "failure_codes": dict(Counter(error for item in results for error in item["errors"])),
                        "document_retrieval": {"hybrid_hit_at_3": sum(item["hybrid_rank_at_3"] is not None for item in docs),
                                               "bm25_hit_at_3": sum(item["bm25_rank_at_3"] is not None for item in docs),
                                               "cases": len(docs),
                                               "hybrid_mrr_at_3": round(sum(1 / item["hybrid_rank_at_3"] for item in docs if item["hybrid_rank_at_3"]) / len(docs), 4) if docs else None,
                                               "bm25_mrr_at_3": round(sum(1 / item["bm25_rank_at_3"] for item in docs if item["bm25_rank_at_3"]) / len(docs), 4) if docs else None}},
            "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_MANUAL_INDEX)
    parser.add_argument("--split", choices=("development", "holdout", "all"), default="all")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = evaluate(args.tasks, args.db, args.index_dir, args.split)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / f"m5_{args.split}_{report['task_sha256'][:12]}_{report['data_snapshot']['snapshot_id']}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(target), **report["summary"]}, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
