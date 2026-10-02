"""Reproducible fetch, index and query commands for versioned manuals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.request import urlopen

from .registry import ManualVersion
from .search import ManualIndex, file_hash

FEATURE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = FEATURE_ROOT.parent
MANIFEST = FEATURE_ROOT / "docs/manual_sources.json"
DEFAULT_INDEX = FEATURE_ROOT / "runtime/manual_index"


def _path(relative: str) -> Path:
    path = (REPO_ROOT / relative).resolve()
    if not path.is_relative_to(FEATURE_ROOT.resolve()):
        raise ValueError("Manual source path must stay inside service_operations")
    return path


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def fetch() -> list[dict]:
    results = []
    for source in _manifest()["documents"]:
        target = _path(source["local_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        expected = source["source_hash"]
        if not target.exists() or file_hash(target) != expected:
            temporary = target.with_suffix(".part")
            try:
                with urlopen(source["source_url"], timeout=60) as response, temporary.open("wb") as output:
                    for block in iter(lambda: response.read(1024 * 1024), b""):
                        output.write(block)
                if file_hash(temporary) != expected:
                    raise ValueError(f"Downloaded PDF hash differs from manifest: {source['logical_doc_id']}")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        results.append({"logical_doc_id": source["logical_doc_id"], "path": str(target), "sha256": expected})
    return results


def versions() -> list[ManualVersion]:
    manifest = _manifest()
    result = []
    for source in manifest["documents"]:
        result.append(ManualVersion(
            logical_doc_id=source["logical_doc_id"], model=source["model"],
            version=source["version"], effective_from=source["effective_from"],
            effective_to=source["effective_to"], source_path=str(_path(source["local_path"])),
            source_hash=source["source_hash"], title=source["title"],
            source_url=source["source_url"], license=source["license"],
        ))
    for source in manifest["demo_notes"]:
        result.append(ManualVersion(
            logical_doc_id=source["logical_doc_id"], model=source["model"],
            version=source["version"], effective_from=source["effective_from"],
            effective_to=source["effective_to"], source_path=str(_path(source["pdf_path"])),
            source_hash=source["source_hash"], title=f"Demo service record note {source['version']}",
            source_url="", license="Project-authored demonstration content", fault_code="NOZZLE_WIPE",
        ))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch", help="Download and SHA-256 verify official PDFs")
    commands.add_parser("index", help="Build both indexes and publish all manifest versions")
    commands.add_parser("list", help="Show the manual registry")
    search = commands.add_parser("search", help="Search only effective published manual versions")
    search.add_argument("--query", required=True)
    search.add_argument("--model", required=True)
    search.add_argument("--as-of", help="Timezone-aware historical datetime")
    search.add_argument("--fault-code")
    search.add_argument("--top-k", type=int, default=5)
    delete = commands.add_parser("delete", help="Delete one physical version and its two indexes")
    delete.add_argument("--logical-doc-id", required=True)
    delete.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            result = fetch()
        else:
            index = ManualIndex(args.index_dir)
            if args.command == "index":
                result = [index.ingest(doc).__dict__ for doc in versions()]
            elif args.command == "list":
                result = [doc.__dict__ for doc in index.registry.list()]
            elif args.command == "search":
                result = index.search(args.query, args.model, as_of=args.as_of,
                                      fault_code=args.fault_code, top_k=args.top_k)
            else:
                result = {"deleted": index.delete(args.logical_doc_id, args.version)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
