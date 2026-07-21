"""Build, verify, query, or serve the small real-data reproduction tier."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil

from app.runtime import build_local_runtime
from retrieval.embed import BGEEncoder
from retrieval.index import build_index, file_sha256
from storage.metadata_db import MetadataDB


ROOT = Path(__file__).resolve().parents[1]
TIER1 = ROOT / "repro" / "tier1"
CHUNKS = TIER1 / "chunks.jsonl"
SOURCES = TIER1 / "sources.csv"
CONFIG = TIER1 / "config.yaml"
SOURCE_MANIFEST = TIER1 / "manifest.json"
RUNTIME = TIER1 / "runtime"
ARTIFACTS = RUNTIME / "artifacts"
METADATA = RUNTIME / "metadata.sqlite3"


def _manifest() -> dict:
    value = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or value.get("tier") != "tier1-real-slice":
        raise RuntimeError("Tier 1 source manifest is incompatible")
    return value


def verify_sources() -> dict:
    manifest = _manifest()
    for name, path in (("chunks.jsonl", CHUNKS), ("sources.csv", SOURCES)):
        expected = manifest["files"][name]
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.stat().st_size != expected["size"]:
            raise RuntimeError(f"Tier 1 size mismatch: {name}")
        if file_sha256(path) != expected["sha256"]:
            raise RuntimeError(f"Tier 1 SHA-256 mismatch: {name}")
    return manifest


def _encoder(manifest: dict, args: argparse.Namespace) -> BGEEncoder:
    model = manifest["model"]
    return BGEEncoder(
        model["id"],
        revision=model["revision"],
        model_path=getattr(args, "model_path", None),
        device=getattr(args, "device", None),
        batch_size=getattr(args, "batch_size", 32),
        use_fp16=False if getattr(args, "fp32", False) else None,
    )


def build(args: argparse.Namespace) -> None:
    manifest = verify_sources()
    if args.clean and RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    index_manifest = build_index(CHUNKS, ARTIFACTS, _encoder(manifest, args))
    metadata = MetadataDB.from_files(
        sources_path=SOURCES,
        chunks_path=CHUNKS,
        database=METADATA,
    )
    count = metadata.connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
    trusted = metadata.connection.execute(
        "SELECT count(*) FROM sources WHERE trusted=1 AND enabled=1"
    ).fetchone()[0]
    metadata.close()
    expected_count = manifest["files"]["chunks.jsonl"]["rows"]
    if count != expected_count or trusted != 5:
        raise RuntimeError(
            f"Tier 1 metadata mismatch: chunks={count}, trusted_sources={trusted}"
        )
    result = {
        "tier": "tier1-real-slice",
        "chunks": count,
        "trusted_sources": trusted,
        "model": manifest["model"],
        "index": index_manifest,
    }
    (RUNTIME / "build-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _runtime(args: argparse.Namespace):
    if not (ARTIFACTS / "manifest.json").is_file():
        raise RuntimeError("Tier 1 is not built; run `python -m scripts.reproduce_tier1 build`")
    os.environ["SWUFE_RAG_ARTIFACTS"] = str(ARTIFACTS)
    if getattr(args, "model_path", None):
        os.environ["SWUFE_RAG_EMBED_MODEL_PATH"] = str(args.model_path)
    return build_local_runtime(
        CHUNKS,
        sources_path=SOURCES,
        metadata_path=METADATA,
        config_path=CONFIG,
    )


def verify(args: argparse.Namespace) -> None:
    manifest = verify_sources()
    runtime = _runtime(args)
    results = runtime.retrieve(
        "计算机科学与技术专业2023级毕业需要修满多少学分？",
        top_k=5,
        college="计算机与人工智能学院",
        cohort="2023",
    )
    if not results:
        raise RuntimeError("Tier 1 smoke query returned no evidence")
    if not any("计算机科学与技术专业2023级" in row["doc_title"] for row in results):
        raise RuntimeError("Tier 1 smoke query missed its authoritative program")
    print(
        json.dumps(
            {
                "status": "ok",
                "chunks": manifest["files"]["chunks.jsonl"]["rows"],
                "top_document": results[0]["doc_title"],
                "top_score": results[0]["score"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def query(args: argparse.Namespace) -> None:
    result = _runtime(args).debug_ask(
        args.question,
        top_k=args.top_k,
        college="计算机与人工智能学院",
        cohort="2023",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def serve(args: argparse.Namespace) -> None:
    verify_sources()
    os.environ.update(
        {
            "SWUFE_RAG_MODE": "local",
            "SWUFE_RAG_CHUNKS": str(CHUNKS),
            "SWUFE_RAG_SOURCES": str(SOURCES),
            "SWUFE_RAG_METADATA": str(METADATA),
            "SWUFE_RAG_ARTIFACTS": str(ARTIFACTS),
            "SWUFE_RAG_CONFIG": str(CONFIG),
        }
    )
    if args.model_path:
        os.environ["SWUFE_RAG_EMBED_MODEL_PATH"] = str(args.model_path)
    from app.debug_server import main

    main()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build", help="build real BGE/FAISS artifacts")
    build_parser.add_argument("--device", default=None)
    build_parser.add_argument("--model-path", type=Path, default=None)
    build_parser.add_argument("--batch-size", type=int, default=32)
    build_parser.add_argument("--fp32", action="store_true")
    build_parser.add_argument("--clean", action="store_true")
    build_parser.set_defaults(handler=build)
    verify_parser = commands.add_parser("verify", help="verify and smoke-query Tier 1")
    verify_parser.add_argument("--model-path", type=Path, default=None)
    verify_parser.set_defaults(handler=verify)
    query_parser = commands.add_parser("query", help="ask without an external LLM key")
    query_parser.add_argument("question")
    query_parser.add_argument("--top-k", type=int, default=5)
    query_parser.add_argument("--model-path", type=Path, default=None)
    query_parser.set_defaults(handler=query)
    serve_parser = commands.add_parser("serve", help="serve the local no-key web demo")
    serve_parser.add_argument("--model-path", type=Path, default=None)
    serve_parser.set_defaults(handler=serve)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
