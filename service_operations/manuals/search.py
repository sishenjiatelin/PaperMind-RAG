"""Page-aware PDF indexing with version-isolated Chroma and BM25 indexes."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from pathlib import Path

import chromadb
import pdfplumber

from src.core.types import Chunk
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.ingestion.storage.bm25_indexer import BM25Indexer

from .registry import ManualRegistry, ManualVersion


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class HashEmbedder:
    """Deterministic local lexical baseline; semantic embeddings can replace this adapter."""

    dimensions = 384

    def __init__(self):
        self.encoder = SparseEncoder(min_term_length=2)

    def embed(self, text: str) -> list[float]:
        terms = self.encoder._tokenize(text)
        counts = Counter(terms)
        vector = [0.0] * self.dimensions
        for term, count in counts.items():
            raw = hashlib.sha256(term.encode()).digest()
            position = int.from_bytes(raw[:4], "big") % self.dimensions
            sign = 1 if raw[4] & 1 else -1
            vector[position] += sign * (1.0 + math.log(count))
        length = math.sqrt(sum(value * value for value in vector))
        if length:
            return [value / length for value in vector]
        return vector


def pdf_chunks(path: Path, namespace: str, *, max_chars: int = 1200, overlap: int = 120) -> list[Chunk]:
    """Split within each physical PDF page so every hit has a verifiable page."""
    result: list[Chunk] = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            page_text = re.sub(r"[ \t]+", " ", page.extract_text() or "").strip()
            if not page_text:
                continue
            offset = 0
            while offset < len(page_text):
                end = min(offset + max_chars, len(page_text))
                if end < len(page_text):
                    boundary = page_text.rfind("\n", offset + max_chars // 2, end)
                    if boundary > offset:
                        end = boundary
                text = page_text[offset:end].strip()
                if text:
                    chunk_id = f"{namespace}_p{page_number}_c{len(result)}"
                    result.append(Chunk(chunk_id, text, {"source_path": str(path), "page": page_number}))
                if end == len(page_text):
                    break
                offset = max(end - overlap, offset + 1)
    if not result:
        raise ValueError(f"No extractable PDF text: {path}")
    return result


class ManualIndex:
    def __init__(self, root: str | Path, embedder: HashEmbedder | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry = ManualRegistry(self.root / "registry.db")
        self.chroma = chromadb.PersistentClient(path=str(self.root / "chroma"))
        self.bm25_dir = self.root / "bm25"
        self.bm25_dir.mkdir(exist_ok=True)
        self.embedder = embedder or HashEmbedder()
        self.encoder = SparseEncoder(min_term_length=2)

    def _indexer(self) -> BM25Indexer:
        return BM25Indexer(index_dir=str(self.bm25_dir))

    def _bm25_path(self, doc: ManualVersion) -> Path:
        return self.bm25_dir / f"{doc.namespace}_bm25.json"

    def _remove_indexes(self, doc: ManualVersion) -> None:
        try:
            self.chroma.delete_collection(doc.namespace)
        except Exception as exc:
            if "does not exist" not in str(exc).lower() and "not found" not in str(exc).lower():
                raise
        self._bm25_path(doc).unlink(missing_ok=True)

    def ingest(self, doc: ManualVersion) -> ManualVersion:
        path = Path(doc.source_path)
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ValueError(f"Source must be an existing PDF: {path}")
        if file_hash(path) != doc.source_hash:
            raise ValueError(f"Source hash mismatch: {path}")
        previous = self.registry.get(doc.logical_doc_id, doc.version)
        if previous and previous.publication_status == "published":
            if previous.source_hash != doc.source_hash:
                raise ValueError("Published version content changed; create a new version")
            if self._bm25_path(previous).exists():
                try:
                    collection = self.chroma.get_collection(previous.namespace)
                    if collection.count() == previous.chunk_count and previous.chunk_count > 0:
                        return previous
                except Exception:
                    pass
            self.registry.set_status(previous, "error")
        staged = self.registry.stage(doc)
        try:
            self._remove_indexes(staged)
            chunks = pdf_chunks(path, staged.namespace)
            collection = self.chroma.create_collection(staged.namespace, metadata={"hnsw:space": "cosine"})
            for offset in range(0, len(chunks), 100):
                batch = chunks[offset:offset + 100]
                collection.add(
                    ids=[chunk.id for chunk in batch],
                    documents=[chunk.text for chunk in batch],
                    embeddings=[self.embedder.embed(chunk.text) for chunk in batch],
                    metadatas=[{"page": chunk.metadata["page"]} for chunk in batch],
                )
            self._indexer().build(self.encoder.encode(chunks), collection=staged.namespace)
            self.registry.set_status(staged, "published", len(chunks))
        except Exception:
            self.registry.set_status(staged, "error")
            self._remove_indexes(staged)
            raise
        return self.registry.get(staged.logical_doc_id, staged.version)

    def delete(self, logical_doc_id: str, version: str) -> bool:
        doc = self.registry.get(logical_doc_id, version)
        if doc is None:
            return False
        self.registry.set_status(doc, "deleting")
        self._remove_indexes(doc)
        self.registry.remove(doc)
        return True

    def search(self, query: str, model: str, *, as_of: str | None = None,
               fault_code: str | None = None, top_k: int = 5) -> list[dict]:
        if not query.strip() or top_k < 1:
            raise ValueError("Nonempty query and positive top_k required")
        selected = self.registry.select(model, as_of, fault_code)
        ranks: dict[str, float] = {}
        provenance: dict[str, tuple[ManualVersion, str]] = {}
        candidate_k = max(top_k * 4, 20)
        query_vector = self.embedder.embed(query)
        query_terms = self.encoder._tokenize(query)
        for doc in selected:
            collection = self.chroma.get_collection(doc.namespace)
            if collection.count() == 0:
                continue
            dense = collection.query(query_embeddings=[query_vector], n_results=min(candidate_k, collection.count()))
            dense_ids = dense["ids"][0]
            indexer = self._indexer()
            if not indexer.load(doc.namespace):
                raise RuntimeError(f"Published version has no BM25 index: {doc.namespace}")
            sparse_ids = [item["chunk_id"] for item in indexer.query(query_terms, top_k=candidate_k)] if query_terms else []
            for branch in (dense_ids, sparse_ids):
                for rank, chunk_id in enumerate(branch, 1):
                    ranks[chunk_id] = ranks.get(chunk_id, 0.0) + 1.0 / (60 + rank)
                    provenance[chunk_id] = (doc, chunk_id)
        # Favor explanatory passages over short index references with the same terms.
        # The version gate has already run for both retrieval branches above.
        evidence: dict[str, tuple[str, int]] = {}
        query_set = set(query_terms)
        exact_codes = [code for code in ("PROBE FAIL CLEAN NOZZLE", "MINTEMP", "MAXTEMP", "HEATING FAILED")
                       if code.lower() in query.lower()]
        for chunk_id, (doc, _) in provenance.items():
            item = self.chroma.get_collection(doc.namespace).get(ids=[chunk_id], include=["documents", "metadatas"])
            if not item["ids"]:
                continue
            passage = item["documents"][0]
            evidence[chunk_id] = (passage, item["metadatas"][0]["page"])
            matched = len(query_set.intersection(self.encoder._tokenize(passage)))
            coverage = matched / max(len(query_set), 1)
            index_references = len(re.findall(r",\s*\d{1,3}\b", passage))
            index_penalty = 0.02 if index_references >= 8 else 0.0
            ranks[chunk_id] += 0.003 * coverage * min(len(passage) / 600, 1.0)
            ranks[chunk_id] += 0.002 * matched - index_penalty
            # An exact displayed error code is stronger evidence than general prose
            # about the same symptom; prefer its troubleshooting section.
            if any(code.lower() in passage.lower() for code in exact_codes):
                ranks[chunk_id] += 0.05
                if "Resolution:" in passage:
                    ranks[chunk_id] += 0.02
        ordered = sorted(evidence, key=lambda key: (-ranks[key], key))[:top_k]
        results = []
        for chunk_id in ordered:
            doc, _ = provenance[chunk_id]
            passage, page = evidence[chunk_id]
            results.append({
                "logical_doc_id": doc.logical_doc_id, "model": doc.model,
                "version": doc.version, "title": doc.title, "source": doc.source_url or doc.source_path,
                "license": doc.license,
                "source_hash": doc.source_hash, "page": page,
                "effective_from": doc.effective_from, "effective_to": doc.effective_to,
                "text": passage, "score": ranks[chunk_id], "chunk_id": chunk_id,
                "matched_terms": len(query_set.intersection(self.encoder._tokenize(passage))),
            })
        return results
