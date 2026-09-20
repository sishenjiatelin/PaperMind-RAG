"""Ground-Truth-aware Evaluation Panel.

The page is intentionally a thin UI over :class:`EvalRunner`. Retrieval,
reranking, answer generation, and evaluator construction come from the
shared Evaluation Core used by the CLI.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

logger = logging.getLogger(__name__)

DEFAULT_BACKEND = "ragas"
DEFAULT_COLLECTION = "av_actress"
DEFAULT_TOP_K = 5
DEFAULT_GOLDEN_SET = Path("tests/fixtures/golden_av_actress.json")
EVAL_HISTORY_PATH = Path("logs/eval_history.jsonl")

RETRIEVAL_METRIC_LABELS = {
    "hit_at_k": "Hit@K",
    "mrr": "MRR",
    "precision_at_k": "Precision@K",
    "recall_at_k": "Recall@K",
    "ndcg_at_k": "nDCG@K",
}
GENERATION_METRIC_LABELS = {
    "faithfulness": "Faithfulness",
    "answer_relevancy": "Answer Relevancy",
    "context_precision": "Context Precision",
    "answer_correctness": "Answer Correctness",
}


def render() -> None:
    """Render configuration, coverage, results, and evaluation history."""
    st.header("📏 Evaluation Panel")
    st.markdown(
        "Run the shared CLI/Dashboard Evaluation Core against a **golden test set**. "
        "Retrieval and generation metrics are shown according to the Ground Truth "
        "fields available in that test set."
    )

    st.subheader("⚙️ Configuration")
    col1, col2, col3 = st.columns(3)
    with col1:
        backend = st.selectbox(
            "Evaluator Backend", options=["ragas", "custom", "composite"], index=0,
            key="eval_backend", help="Ragas is the default production evaluator.",
        )
    with col2:
        top_k = st.number_input(
            "Top-K", min_value=1, max_value=50, value=DEFAULT_TOP_K,
            key="eval_top_k", help="Final context size after reranking.",
        )
    with col3:
        collection = st.text_input(
            "Collection", value=DEFAULT_COLLECTION, key="eval_collection",
            help="Collection bound to the shared HybridSearch pipeline.",
        )

    golden_path_str = st.text_input(
        "Golden Test Set Path", value=str(DEFAULT_GOLDEN_SET),
        key="eval_golden_path", help="JSON file containing queries and Ground Truth.",
    )
    golden_path = Path(golden_path_str)
    _sync_golden_set_state(golden_path)

    if backend in ("custom", "composite"):
        st.info(
            "Retrieval metrics are computed by the shared Evaluation Core when "
            "Ground Truth is present. Ragas remains the recommended production backend.",
            icon="ℹ️",
        )

    test_cases: List[Dict[str, Any]] = []
    if golden_path.exists():
        try:
            test_cases = _load_golden_queries(golden_path)
            _render_ground_truth_coverage(test_cases)
        except Exception as exc:
            st.warning(f"无法加载 Golden Test Set: {exc}")
    else:
        st.warning(f"⚠️ **Golden test set not found:** `{golden_path}`.")

    user_answers = _render_optional_answer_overrides(test_cases, golden_path, backend)

    st.divider()
    run_clicked = st.button(
        "▶️  Run Evaluation", type="primary", key="eval_run_btn",
        disabled=not golden_path.exists(),
    )
    if run_clicked:
        _run_evaluation(
            backend=backend, golden_path=golden_path, top_k=int(top_k),
            collection=collection.strip() or DEFAULT_COLLECTION,
            user_answers=user_answers or None,
        )

    st.divider()
    _render_history()


def _sync_golden_set_state(golden_path: Path) -> None:
    """Clear testcase-scoped state whenever the selected file changes."""
    state_key = str(golden_path.resolve())
    previous = st.session_state.get("_eval_golden_set_state")
    if previous == state_key:
        return

    for key in list(st.session_state.keys()):
        if str(key).startswith("eval_answer_override_"):
            del st.session_state[key]
    st.session_state["_eval_golden_set_state"] = state_key
    st.session_state["eval_manual_answers_enabled"] = False


def _render_optional_answer_overrides(
    test_cases: List[Dict[str, Any]], golden_path: Path, backend: str,
) -> Dict[int, str]:
    """Render manual overrides only as an explicit Advanced option."""
    if backend != "ragas" or not test_cases:
        return {}

    with st.expander("Advanced: Manual Answer Overrides (optional)", expanded=False):
        st.caption(
            "默认关闭。正常 Ragas Evaluation 会调用当前 DeepSeek Answer Generator。"
            "Reference Answer 只用于展示/未来评估输入，不会被当作 generated_answer。"
        )
        enabled = st.checkbox(
            "Enable manual answer overrides", value=False,
            key="eval_manual_answers_enabled",
        )
        if not enabled:
            return {}

        signature = str(golden_path.resolve()).replace("/", "_").replace("\\", "_")
        answers: Dict[int, str] = {}
        for index, test_case in enumerate(test_cases):
            answer = st.text_area(
                f"Q{index + 1}: {test_case.get('query', '')[:80]}",
                value="", key=f"eval_answer_override_{signature}_{index}",
                height=80, placeholder="仅在明确需要时输入人工 Answer Override…",
            )
            if answer.strip():
                answers[index] = answer.strip()
        return answers


def _ground_truth_coverage(test_cases: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count coverage of every currently supported Ground Truth field."""
    total = len(test_cases)
    return {
        "queries": total,
        "reference_answers": sum(bool(item.get("reference_answer")) for item in test_cases),
        "expected_sources": sum(bool(item.get("expected_sources")) for item in test_cases),
        "expected_chunk_ids": sum(bool(item.get("expected_chunk_ids")) for item in test_cases),
        "relevance_judgments": sum(bool(item.get("relevance_judgments")) for item in test_cases),
    }


def _render_ground_truth_coverage(test_cases: List[Dict[str, Any]]) -> None:
    coverage = _ground_truth_coverage(test_cases)
    st.subheader("🎯 Ground Truth Coverage")
    cols = st.columns(5)
    labels = (
        ("Queries", "queries"), ("Reference Answers", "reference_answers"),
        ("Expected Sources", "expected_sources"),
        ("Expected Chunk IDs", "expected_chunk_ids"),
        ("Graded Relevance", "relevance_judgments"),
    )
    for col, (label, key) in zip(cols, labels):
        with col:
            value = coverage[key]
            display = str(value) if key == "queries" else f"{value}/{coverage['queries']}"
            st.metric(label, display)


def _run_evaluation(
    backend: str, golden_path: Path, top_k: int, collection: Optional[str],
    user_answers: Optional[Dict[int, str]] = None,
) -> None:
    with st.spinner("Loading shared Evaluation Core and running evaluation…"):
        try:
            report_dict = _execute_evaluation(
                backend=backend, golden_path=golden_path, top_k=top_k,
                collection=collection, user_answers=user_answers,
            )
        except Exception as exc:
            st.error(f"❌ Evaluation failed: {exc}")
            logger.exception("Evaluation failed")
            return

    st.success("✅ Evaluation complete!")
    _render_aggregate_metrics(report_dict)
    _render_query_details(report_dict)
    _save_to_history(report_dict)


def _execute_evaluation(
    backend: str, golden_path: Path, top_k: int, collection: Optional[str],
    user_answers: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    """Run the same shared pipeline used by ``scripts/evaluate.py``."""
    from dataclasses import replace as dc_replace

    from dotenv import load_dotenv
    from src.core.settings import load_settings
    from src.libs.evaluator.evaluator_factory import EvaluatorFactory
    from src.observability.evaluation.eval_runner import EvalRunner
    from src.observability.evaluation.pipeline import build_evaluation_pipeline

    # The CLI loads the project .env before constructing providers.  Dashboard
    # runs inside Streamlit, so load the same project environment at the shared
    # execution boundary before DeepSeek/Ollama/Rerank are initialized.
    load_dotenv(Path(__file__).resolve().parents[4] / ".env")
    settings = load_settings()
    eval_settings = settings.evaluation
    overridden_eval = type(eval_settings)(
        enabled=True, provider=backend,
        metrics=eval_settings.metrics if hasattr(eval_settings, "metrics") else [],
    )
    settings_with_override = dc_replace(settings, evaluation=overridden_eval)
    evaluator = EvaluatorFactory.create(settings_with_override)

    target_collection = collection or DEFAULT_COLLECTION
    pipeline = build_evaluation_pipeline(settings, target_collection)
    runner = EvalRunner(
        settings=settings_with_override,
        hybrid_search=pipeline.hybrid_search,
        reranker=pipeline.reranker,
        evaluator=evaluator,
        answer_generator=pipeline.answer_generator,
        answer_overrides=user_answers,
    )
    report = runner.run(
        test_set_path=golden_path, top_k=top_k, collection=target_collection,
    )
    return report.to_dict()


def _render_metric_group(
    title: str, labels: Dict[str, str], aggregate: Dict[str, float],
    statuses: Dict[str, str],
) -> None:
    st.markdown(f"### {title}")
    cols = st.columns(min(max(len(labels), 1), 4))
    for index, (metric, label) in enumerate(labels.items()):
        with cols[index % len(cols)]:
            if metric in aggregate:
                st.metric(label, f"{aggregate[metric]:.4f}")
            else:
                reason = statuses.get(metric, "missing Ground Truth")
                st.info(f"{label}\n\nMetric unavailable: {reason}")


def _aggregate_unavailable_statuses(report: Dict[str, Any]) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    for query_result in report.get("query_results", []):
        for metric, reason in query_result.get("metric_status", {}).items():
            statuses.setdefault(metric, reason)
    return statuses


def _render_aggregate_metrics(report: Dict[str, Any]) -> None:
    st.subheader("📊 Aggregate Metrics")
    aggregate = report.get("aggregate_metrics", {})
    statuses = _aggregate_unavailable_statuses(report)
    _render_metric_group("Retrieval Metrics", RETRIEVAL_METRIC_LABELS, aggregate, statuses)
    _render_metric_group("Generation Metrics", GENERATION_METRIC_LABELS, aggregate, statuses)
    st.caption(
        f"Evaluator: **{report.get('evaluator_name', '—')}** · "
        f"Queries: **{report.get('query_count', 0)}** · "
        f"Total time: **{report.get('total_elapsed_ms', 0):.0f} ms**"
    )


def _render_query_details(report: Dict[str, Any]) -> None:
    st.subheader("🔍 Per-Query Details")
    query_results = report.get("query_results", [])
    if not query_results:
        st.info("No per-query results available.")
        return

    for index, query_result in enumerate(query_results):
        query = query_result.get("query", "—")
        with st.expander(f"**Q{index + 1}**: {query[:100]}", expanded=False):
            st.markdown("**Query**")
            st.write(query)
            st.markdown("**Retrieved results**")
            retrieved = query_result.get("retrieved_results", [])
            if retrieved:
                st.dataframe(
                    [
                        {
                            "rank": item.get("rank", rank),
                            "chunk id": item.get("chunk_id", ""),
                            "source": item.get("source", ""),
                            "retrieval score": item.get("retrieval_score", item.get("score", 0.0)),
                            "fusion score": item.get("fusion_score", item.get("score", 0.0)),
                            "rerank score": item.get("rerank_score", item.get("score", 0.0)),
                        }
                        for rank, item in enumerate(retrieved, start=1)
                    ],
                    use_container_width=True,
                )
            else:
                st.info("No retrieved results.")

            st.markdown("**Generated Answer**")
            st.write(query_result.get("generated_answer") or "No generated answer.")
            if query_result.get("reference_answer"):
                st.markdown("**Reference Answer**")
                st.write(query_result["reference_answer"])

            ground_truth = {
                key: query_result.get(key)
                for key in ("expected_sources", "expected_chunk_ids", "relevance_judgments")
                if query_result.get(key)
            }
            if ground_truth:
                st.markdown("**Retrieval Ground Truth**")
                st.json(ground_truth)

            st.markdown("**Per-query metrics**")
            metrics = query_result.get("metrics", {})
            if metrics:
                metric_cols = st.columns(min(len(metrics), 4))
                for metric_index, (metric, value) in enumerate(sorted(metrics.items())):
                    label = RETRIEVAL_METRIC_LABELS.get(
                        metric, GENERATION_METRIC_LABELS.get(metric, metric)
                    )
                    with metric_cols[metric_index % len(metric_cols)]:
                        st.metric(label, f"{value:.4f}")
            for metric, reason in query_result.get("metric_status", {}).items():
                label = RETRIEVAL_METRIC_LABELS.get(metric, metric)
                st.info(f"{label}: Metric unavailable: {reason}")


def _render_history() -> None:
    st.subheader("📈 Evaluation History")
    history = _load_history()
    if not history:
        st.info("**No evaluation history yet.** Configure the evaluator and click Run Evaluation to start.")
        return
    rows = []
    for entry in history[-10:]:
        rows.append(
            {
                "Timestamp": entry.get("timestamp", "—"),
                "Evaluator": entry.get("evaluator_name", "—"),
                "Queries": entry.get("query_count", 0),
                "Time (ms)": round(entry.get("total_elapsed_ms", 0)),
                **{key: round(value, 4) for key, value in entry.get("aggregate_metrics", {}).items()},
            }
        )
    st.dataframe(rows, use_container_width=True)


def _save_to_history(report: Dict[str, Any]) -> None:
    try:
        EVAL_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EVAL_HISTORY_PATH.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), **report},
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception as exc:
        logger.warning("Failed to save evaluation history: %s", exc)


def _load_history() -> List[Dict[str, Any]]:
    if not EVAL_HISTORY_PATH.exists():
        return []
    entries: List[Dict[str, Any]] = []
    try:
        with EVAL_HISTORY_PATH.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    if line.strip():
                        entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        logger.warning("Failed to load evaluation history: %s", exc)
    return entries


def _load_golden_queries(golden_path: Path) -> List[Dict[str, Any]]:
    with golden_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data.get("test_cases", [])
