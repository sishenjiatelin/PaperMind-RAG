"""Tests for the shared CLI/Dashboard answer-generation wiring."""

from __future__ import annotations

from types import SimpleNamespace

from src.observability.evaluation import pipeline


def test_answer_generator_uses_configured_llm_and_context_only(monkeypatch) -> None:
    captured = {}

    class FakeLLM:
        def chat(self, messages, **kwargs):
            captured["messages"] = messages
            captured["kwargs"] = kwargs
            return SimpleNamespace(content="Generated from context")

    monkeypatch.setattr(pipeline.LLMFactory, "create", lambda settings: FakeLLM())
    settings = SimpleNamespace(
        llm=SimpleNamespace(temperature=0.0, max_tokens=128),
    )

    generator = pipeline.build_answer_generator(settings)
    answer = generator("What is Q?", [SimpleNamespace(text="Context only")])

    assert answer == "Generated from context"
    assert captured["messages"][0].role == "system"
    assert "only the retrieved context" in captured["messages"][0].content
    assert "Context only" in captured["messages"][1].content
    assert "reference_answer" not in captured["messages"][1].content
