"""Unit tests for the Evaluation Panel dashboard page."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


class TestEvaluationPanelHelpers:
    """Test helper functions in evaluation_panel module."""

    def test_save_and_load_history(self, tmp_path: Path) -> None:
        """History round-trip: save then load."""
        from src.observability.dashboard.pages import evaluation_panel as ep

        # Temporarily override history path
        original = ep.EVAL_HISTORY_PATH
        ep.EVAL_HISTORY_PATH = tmp_path / "eval_history.jsonl"

        try:
            report = {
                "evaluator_name": "custom",
                "query_count": 2,
                "total_elapsed_ms": 123.4,
                "aggregate_metrics": {"hit_rate": 0.8},
            }

            ep._save_to_history(report)
            history = ep._load_history()

            assert len(history) == 1
            assert history[0]["evaluator_name"] == "custom"
            assert history[0]["aggregate_metrics"]["hit_rate"] == 0.8
            assert "timestamp" in history[0]
        finally:
            ep.EVAL_HISTORY_PATH = original

    def test_load_history_empty(self, tmp_path: Path) -> None:
        """Load returns empty list when no history file exists."""
        from src.observability.dashboard.pages import evaluation_panel as ep

        original = ep.EVAL_HISTORY_PATH
        ep.EVAL_HISTORY_PATH = tmp_path / "nonexistent.jsonl"

        try:
            assert ep._load_history() == []
        finally:
            ep.EVAL_HISTORY_PATH = original

    def test_load_history_tolerates_bad_lines(self, tmp_path: Path) -> None:
        """Malformed lines are skipped."""
        from src.observability.dashboard.pages import evaluation_panel as ep

        original = ep.EVAL_HISTORY_PATH
        hist_file = tmp_path / "eval_history.jsonl"
        hist_file.write_text(
            '{"ok": true}\nBAD LINE\n{"ok": false}\n',
            encoding="utf-8",
        )
        ep.EVAL_HISTORY_PATH = hist_file

        try:
            history = ep._load_history()
            assert len(history) == 2
            assert history[0]["ok"] is True
            assert history[1]["ok"] is False
        finally:
            ep.EVAL_HISTORY_PATH = original

    def test_save_history_creates_parent_dir(self, tmp_path: Path) -> None:
        """_save_to_history creates missing parent directories."""
        from src.observability.dashboard.pages import evaluation_panel as ep

        original = ep.EVAL_HISTORY_PATH
        ep.EVAL_HISTORY_PATH = tmp_path / "subdir" / "eval.jsonl"

        try:
            ep._save_to_history({"test": True})
            assert ep.EVAL_HISTORY_PATH.exists()
        finally:
            ep.EVAL_HISTORY_PATH = original


class TestEvaluationPanelImport:
    """Verify the module can be imported without side effects."""

    def test_module_imports(self) -> None:
        from src.observability.dashboard.pages import evaluation_panel

        assert hasattr(evaluation_panel, "render")
        assert callable(evaluation_panel.render)

    def test_default_golden_path(self) -> None:
        from src.observability.dashboard.pages.evaluation_panel import (
            DEFAULT_BACKEND,
            DEFAULT_COLLECTION,
            DEFAULT_GOLDEN_SET,
            DEFAULT_TOP_K,
        )

        assert DEFAULT_BACKEND == "ragas"
        assert DEFAULT_COLLECTION == "av_actress"
        assert DEFAULT_TOP_K == 5
        assert DEFAULT_GOLDEN_SET == Path("tests/fixtures/golden_av_actress.json")

    def test_ground_truth_coverage(self) -> None:
        from src.observability.dashboard.pages.evaluation_panel import _ground_truth_coverage

        coverage = _ground_truth_coverage([
            {
                "query": "q1",
                "reference_answer": "a1",
                "expected_sources": ["doc.pdf"],
                "expected_chunk_ids": [],
            },
            {
                "query": "q2",
                "reference_answer": "a2",
                "expected_sources": [],
                "expected_chunk_ids": ["chunk-2"],
                "relevance_judgments": {"chunk-2": 3},
            },
        ])

        assert coverage == {
            "queries": 2,
            "reference_answers": 2,
            "expected_sources": 1,
            "expected_chunk_ids": 1,
            "relevance_judgments": 1,
        }

    def test_golden_set_switch_clears_answer_state(self) -> None:
        from src.observability.dashboard.pages import evaluation_panel as ep

        old_session_state = ep.st.session_state
        ep.st.session_state = {
            "_eval_golden_set_state": str(Path("old.json").resolve()),
            "eval_answer_override_old_0": "stale answer",
            "eval_manual_answers_enabled": True,
        }
        try:
            ep._sync_golden_set_state(Path("new.json"))
            assert "eval_answer_override_old_0" not in ep.st.session_state
            assert ep.st.session_state["eval_manual_answers_enabled"] is False
            assert ep.st.session_state["_eval_golden_set_state"] == str(Path("new.json").resolve())
        finally:
            ep.st.session_state = old_session_state

    def test_execute_evaluation_passes_shared_pipeline_components(self, monkeypatch, tmp_path: Path) -> None:
        from src.core.settings import load_settings
        from src.observability.dashboard.pages import evaluation_panel as ep

        settings = load_settings()
        evaluator = object()
        hybrid_search = object()
        reranker = object()
        answer_generator = lambda query, chunks: "generated"
        captured: Dict[str, Any] = {}

        class FakeRunner:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def run(self, **kwargs):
                captured["run_kwargs"] = kwargs
                return SimpleNamespace(to_dict=lambda: {"aggregate_metrics": {}, "query_results": []})

        monkeypatch.setattr("src.core.settings.load_settings", lambda: settings)
        monkeypatch.setattr(
            "src.libs.evaluator.evaluator_factory.EvaluatorFactory.create",
            lambda settings: evaluator,
        )
        monkeypatch.setattr(
            "src.observability.evaluation.pipeline.build_evaluation_pipeline",
            lambda settings, collection: SimpleNamespace(
                hybrid_search=hybrid_search,
                reranker=reranker,
                answer_generator=answer_generator,
            ),
        )
        monkeypatch.setattr(
            "src.observability.evaluation.eval_runner.EvalRunner", FakeRunner,
        )

        golden = tmp_path / "golden.json"
        golden.write_text('{"test_cases": [{"query": "Q"}]}', encoding="utf-8")
        ep._execute_evaluation("ragas", golden, 5, "av_actress")

        assert captured["hybrid_search"] is hybrid_search
        assert captured["reranker"] is reranker
        assert captured["evaluator"] is evaluator
        assert captured["answer_generator"] is answer_generator
        assert captured["run_kwargs"]["collection"] == "av_actress"
