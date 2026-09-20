"""Ground-truth-aware retrieval metric helpers.

The helpers in this module deliberately have no retrieval implementation
dependency.  They consume the final ranked chunk ids produced by EvalRunner,
so adding a metric does not require changing the Dashboard retrieval flow.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Mapping, Sequence


RETRIEVAL_METRICS = (
    "hit_at_k",
    "mrr",
    "precision_at_k",
    "recall_at_k",
    "ndcg_at_k",
)


def relevant_ids(
    expected_chunk_ids: Iterable[str] | None = None,
    relevance_judgments: Mapping[str, float] | None = None,
) -> set[str]:
    """Return binary relevant ids from either supported ground-truth shape."""
    ids = {str(item) for item in (expected_chunk_ids or [])}
    if ids:
        return ids
    return {
        str(chunk_id)
        for chunk_id, relevance in (relevance_judgments or {}).items()
        if float(relevance) > 0
    }


def metric_unavailability(
    metric: str,
    *,
    expected_chunk_ids: Iterable[str] | None = None,
    relevance_judgments: Mapping[str, float] | None = None,
) -> str | None:
    """Explain why a retrieval metric cannot be computed for one query."""
    normalized = metric.lower()
    if normalized == "ndcg_at_k":
        if not relevance_judgments:
            return "missing relevance_judgments"
        return None
    if normalized in {"hit_at_k", "mrr", "precision_at_k", "recall_at_k"}:
        if not expected_chunk_ids and not relevant_ids(None, relevance_judgments):
            return "missing expected_chunk_ids"
        return None
    return f"unsupported metric: {metric}"


def compute_retrieval_metrics(
    retrieved_ids: Sequence[str],
    *,
    top_k: int,
    expected_chunk_ids: Iterable[str] | None = None,
    relevance_judgments: Mapping[str, float] | None = None,
) -> tuple[Dict[str, float], Dict[str, str]]:
    """Compute available retrieval metrics and status for unavailable ones.

    ``expected_chunk_ids`` is the binary relevance contract for Hit/MRR/
    Precision/Recall.  ``relevance_judgments`` additionally enables graded
    nDCG@K and can serve as binary relevance for the other four metrics.
    """
    k = max(1, int(top_k))
    ranked = [str(item) for item in retrieved_ids]
    relevant = relevant_ids(expected_chunk_ids, relevance_judgments)
    metrics: Dict[str, float] = {}
    unavailable: Dict[str, str] = {}

    for metric in RETRIEVAL_METRICS:
        reason = metric_unavailability(
            metric,
            expected_chunk_ids=expected_chunk_ids,
            relevance_judgments=relevance_judgments,
        )
        if reason:
            unavailable[metric] = reason
            continue

        top_results = ranked[:k]
        hits = [index for index, chunk_id in enumerate(top_results) if chunk_id in relevant]
        if metric == "hit_at_k":
            metrics[metric] = 1.0 if hits else 0.0
        elif metric == "mrr":
            metrics[metric] = 1.0 / (hits[0] + 1) if hits else 0.0
        elif metric == "precision_at_k":
            metrics[metric] = len(hits) / k
        elif metric == "recall_at_k":
            metrics[metric] = len(hits) / len(relevant) if relevant else 0.0
        elif metric == "ndcg_at_k":
            judgments = {str(key): float(value) for key, value in (relevance_judgments or {}).items()}
            dcg = sum(
                (2 ** judgments.get(chunk_id, 0.0) - 1) / math.log2(index + 2)
                for index, chunk_id in enumerate(top_results)
            )
            ideal = sorted(judgments.values(), reverse=True)[:k]
            ideal_dcg = sum(
                (2 ** relevance - 1) / math.log2(index + 2)
                for index, relevance in enumerate(ideal)
            )
            metrics[metric] = dcg / ideal_dcg if ideal_dcg else 0.0

    return metrics, unavailable
