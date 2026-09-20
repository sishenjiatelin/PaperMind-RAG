"""Tests for extensible Ground Truth-aware retrieval metrics."""

from __future__ import annotations

from src.observability.evaluation.retrieval_metrics import compute_retrieval_metrics


def test_missing_chunk_ground_truth_is_unavailable() -> None:
    metrics, unavailable = compute_retrieval_metrics(
        ["a", "b"], top_k=5,
    )

    assert metrics == {}
    assert unavailable["hit_at_k"] == "missing expected_chunk_ids"
    assert unavailable["mrr"] == "missing expected_chunk_ids"
    assert unavailable["precision_at_k"] == "missing expected_chunk_ids"
    assert unavailable["recall_at_k"] == "missing expected_chunk_ids"
    assert unavailable["ndcg_at_k"] == "missing relevance_judgments"


def test_expected_chunk_ids_enable_binary_metrics() -> None:
    metrics, unavailable = compute_retrieval_metrics(
        ["wrong", "gold", "other"],
        top_k=3,
        expected_chunk_ids=["gold"],
    )

    assert unavailable["ndcg_at_k"] == "missing relevance_judgments"
    assert metrics["hit_at_k"] == 1.0
    assert metrics["mrr"] == 0.5
    assert metrics["precision_at_k"] == 1 / 3
    assert metrics["recall_at_k"] == 1.0


def test_relevance_judgments_enable_ndcg() -> None:
    metrics, unavailable = compute_retrieval_metrics(
        ["low", "high"],
        top_k=2,
        relevance_judgments={"high": 3, "low": 1},
    )

    assert not unavailable
    assert metrics["hit_at_k"] == 1.0
    assert metrics["mrr"] == 1.0
    assert metrics["ndcg_at_k"] < 1.0
