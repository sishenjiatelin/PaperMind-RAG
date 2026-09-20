"""Shared construction of the production Evaluation Pipeline.

Both ``scripts/evaluate.py`` and the Dashboard use this module.  The module
only wires existing providers together; it does not implement retrieval,
reranking, or a second answer-generation path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.core.query_engine.dense_retriever import create_dense_retriever
from src.core.query_engine.hybrid_search import create_hybrid_search
from src.core.query_engine.query_processor import QueryProcessor
from src.core.query_engine.reranker import create_core_reranker
from src.core.query_engine.sparse_retriever import create_sparse_retriever
from src.ingestion.storage.bm25_indexer import BM25Indexer
from src.libs.embedding.embedding_factory import EmbeddingFactory
from src.libs.llm.base_llm import Message
from src.libs.llm.llm_factory import LLMFactory
from src.libs.vector_store.vector_store_factory import VectorStoreFactory


@dataclass
class EvaluationPipeline:
    """Existing production components required by EvalRunner."""

    hybrid_search: Any
    reranker: Any
    answer_generator: Callable[[str, list[Any]], str]


def build_hybrid_search(settings: Any, collection: str) -> Any:
    """Build the configured Dense + BM25 + RRF search engine."""
    vector_store = VectorStoreFactory.create(settings, collection_name=collection)
    embedding_client = EmbeddingFactory.create(settings)
    dense_retriever = create_dense_retriever(
        settings=settings,
        embedding_client=embedding_client,
        vector_store=vector_store,
    )
    bm25_indexer = BM25Indexer(index_dir=f"data/db/bm25/{collection}")
    sparse_retriever = create_sparse_retriever(
        settings=settings,
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )
    sparse_retriever.default_collection = collection
    return create_hybrid_search(
        settings=settings,
        query_processor=QueryProcessor(),
        dense_retriever=dense_retriever,
        sparse_retriever=sparse_retriever,
    )


def build_answer_generator(settings: Any) -> Callable[[str, list[Any]], str]:
    """Create the same context-grounded LLM answer generator as the CLI."""
    answer_llm = LLMFactory.create(settings)

    def answer_generator(query: str, chunks: list[Any]) -> str:
        context_parts = []
        for chunk in chunks:
            if isinstance(chunk, str):
                context_parts.append(chunk)
            elif isinstance(chunk, dict):
                context_parts.append(
                    str(
                        chunk.get("text")
                        or chunk.get("content")
                        or chunk.get("page_content")
                        or ""
                    )
                )
            elif hasattr(chunk, "text"):
                context_parts.append(str(chunk.text))
            else:
                context_parts.append(str(chunk))

        context = "\n\n".join(part for part in context_parts if part.strip())
        if not context:
            raise ValueError("Cannot generate an answer without retrieved context")

        response = answer_llm.chat(
            [
                Message(
                    role="system",
                    content=(
                        "Answer the user's question using only the retrieved context. "
                        "If the context is insufficient, say so clearly. Do not invent facts. "
                        "Do not use external knowledge or perform network searches."
                    ),
                ),
                Message(
                    role="user",
                    content=f"Question:\n{query}\n\nRetrieved context:\n{context}",
                ),
            ],
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
        )
        if not response.content or not response.content.strip():
            raise ValueError("Configured LLM returned an empty answer")
        return response.content

    return answer_generator


def build_evaluation_pipeline(settings: Any, collection: str) -> EvaluationPipeline:
    """Build the complete pipeline shared by CLI and Dashboard."""
    return EvaluationPipeline(
        hybrid_search=build_hybrid_search(settings, collection),
        reranker=create_core_reranker(settings),
        answer_generator=build_answer_generator(settings),
    )
