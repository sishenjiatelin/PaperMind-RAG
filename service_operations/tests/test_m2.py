"""M2: version constraints, index coordination and legacy deduplication."""

import sqlite3

import pytest

from service_operations.examples.manuals.build_demo_pdfs import build_pdf
from service_operations.manuals.registry import ManualRegistry, ManualVersion
from service_operations.manuals.search import ManualIndex, file_hash
from src.libs.loader.file_integrity import SQLiteIntegrityChecker


def version(tmp_path, name, text, start, end=None):
    path = tmp_path / f"{name}.pdf"
    path.write_bytes(build_pdf([text]))
    return ManualVersion(
        logical_doc_id="demo_note", model="TAZ_PRO", version=name,
        effective_from=start, effective_to=end, source_path=str(path),
        source_hash=file_hash(path), title=name, source_url="", license="project demo",
        fault_code="NOZZLE_WIPE",
    )


def test_version_filter_applies_before_both_top_k_and_delete_is_isolated(tmp_path):
    index = ManualIndex(tmp_path / "index")
    old = version(tmp_path, "v1", "NOZZLE_WIPE nozzle wipe cleaning nozzle wipe old record priority", "2025-10-01T00:00:00+08:00", "2026-07-01T00:00:00+08:00")
    new = version(tmp_path, "v2", "Record printing material and Cura profile name", "2026-07-01T00:00:00+08:00")
    index.ingest(old)
    index.ingest(new)
    question = "NOZZLE_WIPE nozzle wipe cleaning nozzle wipe old record priority"
    current = index.search(question, "TAZ_PRO", as_of="2026-08-01T00:00:00+08:00", fault_code="NOZZLE_WIPE", top_k=1)
    historical = index.search(question, "TAZ_PRO", as_of="2026-06-01T00:00:00+08:00", fault_code="NOZZLE_WIPE", top_k=1)
    assert [row["version"] for row in current] == ["v2"]
    assert [row["version"] for row in historical] == ["v1"]
    assert current[0]["page"] == 1
    assert index.search(question, "TAZ_WORKHORSE", as_of="2026-08-01T00:00:00+08:00") == []
    assert index.delete("demo_note", "v1")
    assert not (index.bm25_dir / f"{old.namespace}_bm25.json").exists()
    assert (index.bm25_dir / f"{new.namespace}_bm25.json").exists()
    assert index.search(question, "TAZ_PRO", as_of="2026-08-01T00:00:00+08:00", fault_code="NOZZLE_WIPE")[0]["version"] == "v2"
    assert index.search(question, "TAZ_PRO", as_of="2026-06-01T00:00:00+08:00", fault_code="NOZZLE_WIPE") == []


def test_registry_rejects_overlap_and_naive_time(tmp_path):
    registry = ManualRegistry(tmp_path / "registry.db")
    old = version(tmp_path, "v1", "old", "2025-10-01T00:00:00+08:00", "2026-07-01T00:00:00+08:00")
    registry.stage(old)
    registry.set_status(old, "published", 1)
    overlapping = version(tmp_path, "v2", "new", "2026-06-30T00:00:00+08:00")
    with pytest.raises(ValueError, match="overlap"):
        registry.stage(overlapping)
    with pytest.raises(ValueError, match="timezone"):
        registry.select("TAZ_PRO", "2026-06-01T00:00:00")


def test_failed_index_is_hidden_and_retryable(tmp_path, monkeypatch):
    index = ManualIndex(tmp_path / "index")
    doc = version(tmp_path, "v1", "some searchable content", "2025-10-01T00:00:00+08:00")
    real_build = index._indexer

    class FailingIndexer:
        def build(self, *args, **kwargs):
            raise RuntimeError("injected BM25 failure")

    monkeypatch.setattr(index, "_indexer", lambda: FailingIndexer())
    with pytest.raises(RuntimeError, match="injected"):
        index.ingest(doc)
    assert index.registry.get("demo_note", "v1").publication_status == "error"
    assert index.registry.select("TAZ_PRO", "2026-01-01T00:00:00+08:00", "NOZZLE_WIPE") == []
    monkeypatch.setattr(index, "_indexer", real_build)
    assert index.ingest(doc).publication_status == "published"


def test_same_hash_is_independent_across_collections_and_legacy_migrates(tmp_path):
    db = tmp_path / "integrity.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE ingestion_history (file_hash TEXT PRIMARY KEY, file_path TEXT NOT NULL,
                     status TEXT NOT NULL, collection TEXT, error_msg TEXT,
                     processed_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        conn.execute("INSERT INTO ingestion_history VALUES ('abc','/old.pdf','success','old',NULL,'2025','2025')")
    checker = SQLiteIntegrityChecker(str(db))
    assert checker.should_skip("abc", "old")
    assert not checker.should_skip("abc", "new")
    checker.mark_success("abc", "/new.pdf", "new")
    assert checker.should_skip("abc", "new")
    assert checker.remove_record("abc", "old")
    assert not checker.should_skip("abc", "old")
    assert checker.should_skip("abc", "new")
