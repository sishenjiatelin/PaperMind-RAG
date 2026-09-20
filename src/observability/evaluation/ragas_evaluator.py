"""Ragas-based evaluator for RAG quality assessment.

This evaluator wraps the Ragas framework to compute LLM-as-Judge metrics:
- Faithfulness: Does the answer stick to the retrieved context?
- Answer Relevancy: Is the answer relevant to the query?
- Context Precision: Are the retrieved chunks relevant and well-ordered?

Design Principles:
- Pluggable: Implements BaseEvaluator interface, swappable via factory.
- Config-Driven: LLM/Embedding backend read from settings.yaml.
- Graceful Degradation: Clear ImportError if ragas not installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from src.libs.evaluator.base_evaluator import BaseEvaluator

logger = logging.getLogger(__name__)

# Metric name constants
FAITHFULNESS = "faithfulness"
ANSWER_RELEVANCY = "answer_relevancy"
CONTEXT_PRECISION = "context_precision"

SUPPORTED_METRICS = {FAITHFULNESS, ANSWER_RELEVANCY, CONTEXT_PRECISION}


def _import_ragas() -> None:
    """Validate that ragas is importable, raising a clear error if not."""
    try:
        import ragas  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "The 'ragas' package is required for RagasEvaluator. "
            "Install it with: pip install ragas datasets"
        ) from exc


class RagasEvaluator(BaseEvaluator):
    """Evaluator that uses the Ragas framework for LLM-as-Judge metrics.

    Ragas does NOT require ground-truth labels.  It uses an LLM to judge
    the quality of the generated answer against the retrieved context.

    Supported metrics:
        - faithfulness: Measures factual consistency with context.
        - answer_relevancy: Measures how relevant the answer is to the query.
        - context_precision: Measures relevance/ordering of retrieved chunks.

    Example::

        evaluator = RagasEvaluator(settings=settings)
        metrics = evaluator.evaluate(
            query="What is RAG?",
            retrieved_chunks=[{"id": "c1", "text": "RAG is ..."}],
            generated_answer="RAG stands for ...",
        )
        # metrics == {"faithfulness": 0.95, "answer_relevancy": 0.88, ...}
    """

    # EvalRunner uses this marker to reject an invalid no-generator setup
    # instead of silently turning retrieved context into an answer.
    requires_generated_answer = True

    def __init__(
        self,
        settings: Any = None,
        metrics: Optional[Sequence[str]] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize RagasEvaluator.

        Args:
            settings: Application settings (used to configure LLM backend).
            metrics: Metric names to compute. Defaults to all supported.
            **kwargs: Additional parameters (reserved).

        Raises:
            ImportError: If ragas is not installed.
            ValueError: If unsupported metric names are requested.
        """
        _import_ragas()

        self.settings = settings
        self.kwargs = kwargs

        if metrics is None:
            metrics = self._metrics_from_settings(settings)

        normalised = [m.strip().lower() for m in (metrics or [])]
        if not normalised:
            normalised = sorted(SUPPORTED_METRICS)

        unsupported = [m for m in normalised if m not in SUPPORTED_METRICS]
        if unsupported:
            raise ValueError(
                f"Unsupported ragas metrics: {', '.join(unsupported)}. "
                f"Supported: {', '.join(sorted(SUPPORTED_METRICS))}"
            )

        self._metric_names = normalised

    # ── public API ────────────────────────────────────────────────

    def evaluate(
        self,
        query: str,
        retrieved_chunks: List[Any],
        generated_answer: Optional[str] = None,
        ground_truth: Optional[Any] = None,
        trace: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, float]:
        """Evaluate RAG quality using Ragas LLM-as-Judge metrics.

        Args:
            query: The user query string.
            retrieved_chunks: Retrieved chunks (dicts with 'text' key or strings).
            generated_answer: The generated answer text. Required for Ragas.
            ground_truth: Ignored by Ragas (not needed for LLM-as-Judge).
            trace: Optional TraceContext for observability.
            **kwargs: Additional parameters.

        Returns:
            Dictionary mapping metric names to float scores (0.0 – 1.0).

        Raises:
            ValueError: If query/chunks are invalid or generated_answer is missing.
        """
        self.validate_query(query)
        self.validate_retrieved_chunks(retrieved_chunks)

        if not generated_answer or not generated_answer.strip():
            raise ValueError(
                "RagasEvaluator requires a non-empty 'generated_answer'. "
                "Ragas uses LLM-as-Judge and needs the answer text to evaluate."
            )

        contexts = self._extract_texts(retrieved_chunks)

        try:
            result = self._run_ragas(query, contexts, generated_answer)
        except Exception as exc:
            logger.error("Ragas evaluation failed: %s", exc, exc_info=True)
            raise RuntimeError(f"Ragas evaluation failed: {exc}") from exc

        return result

    # ── private helpers ───────────────────────────────────────────

    def _run_ragas(
        self,
        query: str,
        contexts: List[str],
        answer: str,
    ) -> Dict[str, float]:
        """Execute Ragas collections metrics and return normalised scores.

        Ragas 0.4+ collections metrics use per-metric ``score()`` instead of
        the legacy ``evaluate()`` pipeline.  Each metric has its own signature:
        - Faithfulness / ContextPrecision: (user_input, response, retrieved_contexts)
        - AnswerRelevancy: (user_input, response)
        """
        from ragas.metrics.collections import (
            AnswerRelevancy,
            ContextPrecisionWithoutReference,
            Faithfulness,
        )

        # Build LLM / Embedding wrappers from settings
        llm, embeddings = self._build_wrappers()

        scores: Dict[str, float] = {}

        for metric_name in self._metric_names:
            if metric_name == FAITHFULNESS:
                m = Faithfulness(llm=llm)
                result = m.score(
                    user_input=query, response=answer, retrieved_contexts=contexts,
                )
            elif metric_name == ANSWER_RELEVANCY:
                m = AnswerRelevancy(llm=llm, embeddings=embeddings)
                result = m.score(user_input=query, response=answer)
            elif metric_name == CONTEXT_PRECISION:
                m = ContextPrecisionWithoutReference(llm=llm)
                result = m.score(
                    user_input=query, response=answer, retrieved_contexts=contexts,
                )
            else:
                continue

            scores[metric_name] = float(result.value) if result.value is not None else 0.0

        return scores

    def _build_wrappers(self) -> tuple:
        """Build Ragas LLM and Embedding wrappers from project settings.

        Uses Ragas 0.4+ native collection metrics. OpenAI/Azure continue to
        use Ragas' native clients; DeepSeek/Ollama use adapters around the
        project's existing Provider factories.

        Returns:
            Tuple of (llm_wrapper, embeddings_wrapper).
        """
        if self.settings is None:
            raise ValueError("Settings required to create LLM for Ragas evaluation")

        # ── LLM ──
        llm_cfg = self.settings.llm
        provider = llm_cfg.provider.lower()

        # DeepSeek is intentionally created through the project's provider
        # factory.  The adapter below only translates Ragas structured-output
        # requests to the existing BaseLLM interface; it does not implement a
        # second DeepSeek client.
        if provider == "deepseek":
            from src.libs.llm.llm_factory import LLMFactory

            project_llm = LLMFactory.create(self.settings)
            llm = self._project_llm_wrapper(project_llm, provider, llm_cfg.model)
        else:
            from openai import AsyncAzureOpenAI, AsyncOpenAI
            from ragas.llms import llm_factory

            llm_azure_endpoint = getattr(llm_cfg, "azure_endpoint", None)

            # Azure-compatible mode: if azure_endpoint is configured, use Azure
            # client even when provider is "openai" (matches project convention).
            use_azure_llm = (
                provider == "azure"
                or (provider == "openai" and llm_azure_endpoint)
            )

            if use_azure_llm:
                llm_client = AsyncAzureOpenAI(
                    api_key=llm_cfg.api_key,
                    azure_endpoint=llm_azure_endpoint or llm_cfg.azure_endpoint,
                    api_version=getattr(llm_cfg, "api_version", None) or "2024-02-15-preview",
                )
            elif provider == "openai":
                llm_client = AsyncOpenAI(api_key=llm_cfg.api_key)
            else:
                raise ValueError(
                    f"Unsupported LLM provider for Ragas: '{provider}'. "
                    "Supported: azure, openai, deepseek"
                )

            # Keep the existing OpenAI/Azure Ragas integration unchanged.
            llm = llm_factory(llm_cfg.model, client=llm_client, max_tokens=8192)

        # ── Embeddings ──
        emb_cfg = self.settings.embedding
        emb_provider = emb_cfg.provider.lower()
        if emb_provider == "ollama":
            from src.libs.embedding.embedding_factory import EmbeddingFactory

            project_embedding = EmbeddingFactory.create(self.settings)
            embeddings = self._project_embedding_wrapper(project_embedding)
        else:
            from openai import AsyncAzureOpenAI, AsyncOpenAI
            from ragas.embeddings import OpenAIEmbeddings

            emb_azure_endpoint = getattr(emb_cfg, "azure_endpoint", None)

            # Same Azure-compatible mode detection for embeddings.
            use_azure_emb = (
                emb_provider == "azure"
                or (emb_provider == "openai" and emb_azure_endpoint)
            )

            if use_azure_emb:
                emb_client = AsyncAzureOpenAI(
                    api_key=emb_cfg.api_key,
                    azure_endpoint=emb_azure_endpoint or emb_cfg.azure_endpoint,
                    api_version=getattr(emb_cfg, "api_version", None) or "2024-02-15-preview",
                )
            elif emb_provider == "openai":
                emb_client = AsyncOpenAI(api_key=emb_cfg.api_key)
            else:
                raise ValueError(
                    f"Unsupported embedding provider for Ragas: '{emb_provider}'. "
                    "Supported: azure, openai, ollama"
                )

            embeddings = OpenAIEmbeddings(model=emb_cfg.model, client=emb_client)

        return llm, embeddings

    @staticmethod
    def _project_llm_wrapper(project_llm: Any, provider: str, model: str) -> Any:
        """Adapt an existing project LLM to Ragas structured-output calls."""
        from ragas.llms.base import InstructorBaseRagasLLM

        from src.libs.llm.base_llm import Message

        class ProjectRagasLLM(InstructorBaseRagasLLM):
            def __init__(self) -> None:
                self.project_llm = project_llm
                self.provider = provider
                self.model = model

            def generate(self, prompt: str, response_model: Any) -> Any:
                response = self.project_llm.chat(
                    [
                        Message(
                            role="system",
                            content=(
                                "Return only valid JSON matching the requested output "
                                "schema. Do not include markdown fences or commentary."
                            ),
                        ),
                        Message(role="user", content=prompt),
                    ],
                    temperature=getattr(self.project_llm, "default_temperature", 0.0),
                    max_tokens=getattr(self.project_llm, "default_max_tokens", 8192),
                )
                return RagasEvaluator._parse_structured_response(
                    response.content, response_model
                )

            async def agenerate(self, prompt: str, response_model: Any) -> Any:
                return await asyncio.to_thread(self.generate, prompt, response_model)

        return ProjectRagasLLM()

    @staticmethod
    def _project_embedding_wrapper(project_embedding: Any) -> Any:
        """Adapt an existing project embedding provider to Ragas embeddings."""
        from ragas.embeddings.base import BaseRagasEmbedding

        class ProjectRagasEmbedding(BaseRagasEmbedding):
            def __init__(self) -> None:
                super().__init__()
                self.project_embedding = project_embedding

            def embed_text(self, text: str, **kwargs: Any) -> List[float]:
                return self.project_embedding.embed([text])[0]

            async def aembed_text(self, text: str, **kwargs: Any) -> List[float]:
                return await asyncio.to_thread(self.embed_text, text, **kwargs)

        return ProjectRagasEmbedding()

    @staticmethod
    def _parse_structured_response(content: str, response_model: Any) -> Any:
        """Parse a provider response into the Pydantic model requested by Ragas."""
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            text = "\n".join(lines).strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            # Some otherwise valid model responses add a short sentence around
            # the JSON. Extracting the object preserves strict model validation
            # without inventing any evaluation result.
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("Ragas judge returned non-JSON structured output")
            payload = json.loads(text[start : end + 1])

        if hasattr(response_model, "model_validate"):
            return response_model.model_validate(payload)
        return response_model.parse_obj(payload)

    def _extract_texts(self, chunks: List[Any]) -> List[str]:
        """Extract text strings from various chunk representations.

        Args:
            chunks: List of chunk dicts, strings, or objects with .text.

        Returns:
            List of text strings.
        """
        texts: List[str] = []
        for chunk in chunks:
            if isinstance(chunk, str):
                texts.append(chunk)
            elif isinstance(chunk, dict):
                text = chunk.get("text") or chunk.get("content") or chunk.get("page_content", "")
                texts.append(str(text))
            elif hasattr(chunk, "text"):
                texts.append(str(getattr(chunk, "text")))
            else:
                texts.append(str(chunk))
        return texts

    def _metrics_from_settings(self, settings: Any) -> List[str]:
        """Extract metrics list from settings if available."""
        if settings is None:
            return []
        evaluation = getattr(settings, "evaluation", None)
        if evaluation is None:
            return []
        raw_metrics = getattr(evaluation, "metrics", None)
        if raw_metrics is None:
            return []
        # Filter to only ragas-supported metrics
        return [m for m in raw_metrics if m.lower() in SUPPORTED_METRICS]
