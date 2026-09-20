"""Evaluation runner for batch quality assessment.

EvalRunner reads a golden test set, runs HybridSearch for each test case,
optionally generates answers, then invokes the configured Evaluator(s) to
produce a structured evaluation report.

Design Principles:
- Config-Driven: Evaluator selected via settings.yaml.
- Observable: Produces EvalReport with per-query details.
- Decoupled: Works with any BaseEvaluator implementation.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.libs.evaluator.base_evaluator import BaseEvaluator
from src.observability.evaluation.retrieval_metrics import compute_retrieval_metrics

logger = logging.getLogger(__name__)


@dataclass
class GoldenTestCase:
    """A single evaluation test case from the golden test set.

    Attributes:
        query: The test query string.
        expected_chunk_ids: Ground-truth chunk IDs for IR metrics.
        expected_sources: Ground-truth source file names (optional).
        reference_answer: Reference answer text for LLM-as-Judge (optional).
    """

    query: str
    expected_chunk_ids: List[str] = field(default_factory=list)
    expected_sources: List[str] = field(default_factory=list)
    reference_answer: Optional[str] = None
    relevance_judgments: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GoldenTestCase:
        return cls(
            query=data["query"],
            expected_chunk_ids=data.get("expected_chunk_ids", []),
            expected_sources=data.get("expected_sources", []),
            reference_answer=data.get("reference_answer"),
            relevance_judgments={
                str(key): float(value)
                for key, value in (data.get("relevance_judgments") or {}).items()
            },
        )


@dataclass
class QueryResult:
    """Result of evaluating a single test case.

    Attributes:
        query: The test query.
        retrieved_chunk_ids: IDs of chunks actually retrieved.
        generated_answer: The generated answer (if applicable).
        metrics: Evaluation metrics for this query.
        elapsed_ms: Time taken for retrieval + evaluation.
    """

    query: str
    retrieved_chunk_ids: List[str] = field(default_factory=list)
    retrieved_results: List[Dict[str, Any]] = field(default_factory=list)
    generated_answer: Optional[str] = None
    reference_answer: Optional[str] = None
    expected_sources: List[str] = field(default_factory=list)
    expected_chunk_ids: List[str] = field(default_factory=list)
    relevance_judgments: Dict[str, float] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)
    metric_status: Dict[str, str] = field(default_factory=dict)
    elapsed_ms: float = 0.0


@dataclass
class EvalReport:
    """Aggregated evaluation report across all test cases.

    Attributes:
        query_results: Per-query evaluation results.
        aggregate_metrics: Averaged metrics across all queries.
        total_elapsed_ms: Total time for the entire evaluation.
        evaluator_name: Name of the evaluator used.
        test_set_path: Path to the golden test set file.
    """

    query_results: List[QueryResult] = field(default_factory=list)
    aggregate_metrics: Dict[str, float] = field(default_factory=dict)
    total_elapsed_ms: float = 0.0
    evaluator_name: str = ""
    test_set_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialise report to dictionary."""
        return {
            "evaluator_name": self.evaluator_name,
            "test_set_path": self.test_set_path,
            "total_elapsed_ms": round(self.total_elapsed_ms, 1),
            "aggregate_metrics": {
                k: round(v, 4) for k, v in self.aggregate_metrics.items()
            },
            "query_count": len(self.query_results),
            "query_results": [
                {
                    "query": qr.query,
                    "retrieved_chunk_ids": qr.retrieved_chunk_ids,
                    "retrieved_results": qr.retrieved_results,
                    "generated_answer": qr.generated_answer,
                    "reference_answer": qr.reference_answer,
                    "expected_sources": qr.expected_sources,
                    "expected_chunk_ids": qr.expected_chunk_ids,
                    "relevance_judgments": qr.relevance_judgments,
                    "metrics": {k: round(v, 4) for k, v in qr.metrics.items()},
                    "metric_status": qr.metric_status,
                    "elapsed_ms": round(qr.elapsed_ms, 1),
                }
                for qr in self.query_results
            ],
        }


def load_test_set(path: str | Path) -> List[GoldenTestCase]:
    """Load golden test set from a JSON file.

    Args:
        path: Path to the golden test set JSON file.

    Returns:
        List of TestCase instances.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file format is invalid.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Golden test set not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if "test_cases" not in data:
        raise ValueError(
            "Invalid golden test set format: missing 'test_cases' key."
        )

    return [GoldenTestCase.from_dict(tc) for tc in data["test_cases"]]


class EvalRunner:
    """Runs batch evaluation against a golden test set.

    This class orchestrates:
    1. Loading the golden test set
    2. Running HybridSearch for each query
    3. Optionally generating answers
    4. Invoking the evaluator to score each result
    5. Aggregating metrics into an EvalReport

    Example::

        runner = EvalRunner(
            settings=settings,
            hybrid_search=hybrid_search,
            evaluator=evaluator,
        )
        report = runner.run("tests/fixtures/golden_test_set.json")
        print(report.aggregate_metrics)
    """

    def __init__(
        self,
        settings: Any = None,
        hybrid_search: Any = None,
        evaluator: Optional[BaseEvaluator] = None,
        answer_generator: Any = None,
        answer_overrides: Optional[Dict[int, str]] = None,
        reranker: Any = None,
    ) -> None:
        """Initialize EvalRunner.

        Args:
            settings: Application settings.
            hybrid_search: HybridSearch instance for retrieval.
            evaluator: BaseEvaluator instance for scoring.
            answer_generator: Optional callable(query, chunks) -> str
                for generating answers. Ragas evaluation requires this to be
                provided; retrieved context is never used as a fake answer.
            answer_overrides: Optional dict mapping test case index (0-based)
                to a user-provided answer string. When present, the override
                answer is used instead of auto-generation for that test case.
            reranker: Optional CoreReranker instance for reranking results.
        """
        self.settings = settings
        self.hybrid_search = hybrid_search
        self.evaluator = evaluator
        self.answer_generator = answer_generator
        self.answer_overrides = answer_overrides or {}
        self.reranker = reranker

    def run(
        self,
        test_set_path: str | Path,
        top_k: int = 10,
        collection: Optional[str] = None,
    ) -> EvalReport:
        """Run evaluation on the golden test set.

        Args:
            test_set_path: Path to golden_test_set.json.
            top_k: Number of chunks to retrieve per query.
            collection: Optional collection name filter.

        Returns:
            EvalReport with per-query and aggregate metrics.

        Raises:
            FileNotFoundError: If test set file doesn't exist.
            ValueError: If evaluator or hybrid_search is not set.
        """
        if self.evaluator is None:
            raise ValueError("EvalRunner requires an evaluator.")

        test_cases = load_test_set(test_set_path)
        if not test_cases:
            raise ValueError("Golden test set is empty.")

        logger.info(
            "Starting evaluation: %d test cases, evaluator=%s",
            len(test_cases),
            type(self.evaluator).__name__,
        )

        report = EvalReport(
            evaluator_name=type(self.evaluator).__name__,
            test_set_path=str(test_set_path),
        )

        t0 = time.monotonic()

        for idx, tc in enumerate(test_cases):
            logger.info("Evaluating [%d/%d]: %s", idx + 1, len(test_cases), tc.query[:60])
            # Use user-provided answer override if available for this index
            answer_override = self.answer_overrides.get(idx)
            qr = self._evaluate_single(
                tc, top_k=top_k, collection=collection,
                answer_override=answer_override,
            )
            report.query_results.append(qr)

        report.total_elapsed_ms = (time.monotonic() - t0) * 1000.0
        report.aggregate_metrics = self._aggregate_metrics(report.query_results)

        logger.info(
            "Evaluation complete: %d queries, aggregate=%s",
            len(report.query_results),
            report.aggregate_metrics,
        )

        return report

    def _evaluate_single(
        self,
        test_case: GoldenTestCase,
        top_k: int = 10,
        collection: Optional[str] = None,
        answer_override: Optional[str] = None,
    ) -> QueryResult:
        """Evaluate a single test case.

        Args:
            test_case: The test case to evaluate.
            top_k: Number of results to retrieve.
            collection: Optional collection filter.
            answer_override: User-provided answer text. When set, used
                instead of auto-generated answer from chunks.

        Returns:
            QueryResult with metrics for this test case.
        """
        t0 = time.monotonic()
        qr = QueryResult(query=test_case.query)
        qr.reference_answer = test_case.reference_answer
        qr.expected_sources = list(test_case.expected_sources)
        qr.expected_chunk_ids = list(test_case.expected_chunk_ids)
        qr.relevance_judgments = dict(test_case.relevance_judgments)

        # Step 1: Retrieve chunks
        retrieved_chunks = self._retrieve(test_case.query, top_k, collection)
        qr.retrieved_chunk_ids = [
            self._get_chunk_id(c) for c in retrieved_chunks
        ]
        qr.retrieved_results = [self._serialize_retrieved_result(c) for c in retrieved_chunks]
        for rank, result in enumerate(qr.retrieved_results, start=1):
            result["rank"] = rank

        retrieval_metrics, unavailable = compute_retrieval_metrics(
            qr.retrieved_chunk_ids,
            top_k=top_k,
            expected_chunk_ids=test_case.expected_chunk_ids,
            relevance_judgments=test_case.relevance_judgments,
        )
        qr.metrics.update(retrieval_metrics)
        qr.metric_status.update(unavailable)

        # Step 2: Generate answer — prefer user override, then the real generator.
        if answer_override:
            answer = answer_override
        else:
            answer = self._generate_answer(test_case.query, retrieved_chunks)
        qr.generated_answer = answer

        # Step 3: Build ground truth
        ground_truth = (
            {"ids": test_case.expected_chunk_ids}
            if test_case.expected_chunk_ids
            else None
        )

        # Step 4: Evaluate
        try:
            metrics = self.evaluator.evaluate(  # type: ignore[union-attr]
                query=test_case.query,
                retrieved_chunks=retrieved_chunks,
                generated_answer=answer,
                ground_truth=ground_truth,
            )
            qr.metrics.update(metrics)
        except Exception as exc:
            logger.warning("Evaluation failed for '%s': %s", test_case.query[:40], exc)
            # Keep shared retrieval metrics even when an optional evaluator
            # backend cannot score this query.

        qr.elapsed_ms = (time.monotonic() - t0) * 1000.0
        return qr

    def _retrieve(
        self,
        query: str,
        top_k: int,
        collection: Optional[str],
    ) -> List[Any]:
        """Retrieve chunks using HybridSearch + optional Reranking.

        Falls back to an empty list if search is not configured.
        """
        if self.hybrid_search is None:
            logger.warning("No HybridSearch configured; returning empty results.")
            return []

        try:
            # Retrieve more candidates if reranker is enabled
            has_reranker = self.reranker is not None and getattr(self.reranker, 'is_enabled', False)
            initial_top_k = top_k * 2 if has_reranker else top_k

            results = self.hybrid_search.search(
                query=query,
                top_k=initial_top_k,
            )
            results = results if isinstance(results, list) else results.results

            # Apply reranking if enabled
            if has_reranker and results:
                rerank_result = self.reranker.rerank(query=query, results=results, top_k=top_k)
                results = rerank_result.results

            return results
        except Exception as exc:
            logger.warning("Retrieval failed for '%s': %s", query[:40], exc)
            return []

    def _generate_answer(self, query: str, chunks: List[Any]) -> Optional[str]:
        """Generate an answer from retrieved chunks.

        A Ragas evaluator must receive an answer generated by the configured
        answer model. It is invalid to concatenate retrieved chunks as a
        substitute, so missing or failed generation is surfaced explicitly.
        """
        if self.answer_generator is None:
            if getattr(self.evaluator, "requires_generated_answer", False):
                raise ValueError(
                    "EvalRunner requires an answer_generator for Ragas evaluation; "
                    "retrieved context cannot be used as generated_answer."
                )
            return None

        try:
            answer = self.answer_generator(query, chunks)
        except Exception as exc:
            raise RuntimeError(f"Answer generation failed: {exc}") from exc

        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Answer generator returned an empty answer")
        return answer

    def _get_chunk_id(self, chunk: Any) -> str:
        """Extract chunk ID from various representations."""
        if isinstance(chunk, str):
            return chunk
        if isinstance(chunk, dict):
            for key in ("id", "chunk_id"):
                if key in chunk:
                    return str(chunk[key])
            return str(chunk)
        if hasattr(chunk, "chunk_id"):
            return str(getattr(chunk, "chunk_id"))
        if hasattr(chunk, "id"):
            return str(getattr(chunk, "id"))
        return str(chunk)

    def _serialize_retrieved_result(self, chunk: Any) -> Dict[str, Any]:
        """Expose ranked retrieval data without coupling the Dashboard to types."""
        chunk_id = self._get_chunk_id(chunk)
        metadata: Dict[str, Any] = {}
        if isinstance(chunk, dict):
            metadata = dict(chunk.get("metadata") or {})
            score = chunk.get("score", 0.0)
        else:
            metadata = dict(getattr(chunk, "metadata", {}) or {})
            score = getattr(chunk, "score", 0.0)

        try:
            final_score = float(score)
        except (TypeError, ValueError):
            final_score = 0.0

        return {
            "rank": 0,  # assigned below after the full list is built
            "chunk_id": chunk_id,
            "source": metadata.get("source_path", metadata.get("source", "")),
            "score": final_score,
            "retrieval_score": metadata.get("original_score", final_score),
            "fusion_score": metadata.get("original_score", final_score),
            "rerank_score": metadata.get("rerank_score", final_score),
        }

    @staticmethod
    def _aggregate_metrics(results: List[QueryResult]) -> Dict[str, float]:
        """Compute average metrics across all query results.

        Args:
            results: List of QueryResult with per-query metrics.

        Returns:
            Dictionary of average metric values.
        """
        if not results:
            return {}

        # Collect all metric keys
        all_keys: set[str] = set()
        for qr in results:
            all_keys.update(qr.metrics.keys())

        # Average each metric
        averages: Dict[str, float] = {}
        for key in sorted(all_keys):
            values = [qr.metrics[key] for qr in results if key in qr.metrics]
            averages[key] = sum(values) / len(values) if values else 0.0

        return averages
