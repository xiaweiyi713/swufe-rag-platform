"""Build a staged retrieval index by reusing a verified frozen prefix."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from retrieval.embed import BGEEncoder, normalize_rows
from retrieval.index import (
    CHUNK_IDS_FILE,
    CHUNKS_FILE,
    FAISS_FILE,
    MANIFEST_FILE,
    VECTORS_FILE,
    file_sha256,
    load_chunks,
)


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    os.replace(temporary, path)


def extend_index(
    baseline_chunks_path: str | Path,
    merged_chunks_path: str | Path,
    baseline_artifacts_path: str | Path,
    output_path: str | Path,
    *,
    model_path: str | Path | None,
    device: str | None,
    batch_size: int,
) -> dict[str, Any]:
    baseline_chunks_file = Path(baseline_chunks_path)
    merged_chunks_file = Path(merged_chunks_path)
    baseline_artifacts = Path(baseline_artifacts_path)
    output = Path(output_path)
    manifest = json.loads(
        (baseline_artifacts / MANIFEST_FILE).read_text(encoding="utf-8")
    )
    if manifest.get("chunks_sha256") != file_sha256(baseline_chunks_file):
        raise ValueError("baseline chunks do not match the retrieval manifest")
    if manifest.get("backend") != "faiss-index-flat-ip":
        raise ValueError("baseline artifacts are not a production FAISS index")

    baseline = load_chunks(baseline_chunks_file)
    merged = load_chunks(merged_chunks_file)
    if len(merged) <= len(baseline) or merged[: len(baseline)] != baseline:
        raise ValueError("merged chunks must preserve the complete baseline as an exact prefix")
    saved_ids = json.loads(
        (baseline_artifacts / CHUNK_IDS_FILE).read_text(encoding="utf-8")
    )
    baseline_ids = [chunk["chunk_id"] for chunk in baseline]
    if saved_ids != baseline_ids:
        raise ValueError("baseline chunk ID artifact is inconsistent")

    vectors = np.load(baseline_artifacts / VECTORS_FILE, mmap_mode="r")
    dimension = int(manifest["dimension"])
    if vectors.shape != (len(baseline), dimension):
        raise ValueError("baseline vector shape is inconsistent")

    try:
        import faiss
    except ImportError as exc:
        raise RuntimeError("faiss-cpu is required to extend the production index") from exc
    index = faiss.read_index(str(baseline_artifacts / FAISS_FILE))
    if index.ntotal != len(baseline) or index.d != dimension:
        raise ValueError("baseline FAISS index is inconsistent")

    additions = merged[len(baseline) :]
    encoder = BGEEncoder(
        str(Path(model_path).expanduser().resolve())
        if model_path is not None
        else str(manifest["model_name"]),
        device=device,
        batch_size=batch_size,
    )
    new_vectors = normalize_rows(
        encoder.encode_documents([chunk["text"] for chunk in additions])
    )
    if new_vectors.shape != (len(additions), dimension):
        raise ValueError(
            f"new vector shape mismatch: {new_vectors.shape}, "
            f"expected {(len(additions), dimension)}"
        )

    output.mkdir(parents=True, exist_ok=True)
    vector_target = output / VECTORS_FILE
    temporary_vectors = vector_target.with_suffix(vector_target.suffix + ".tmp")
    combined = np.lib.format.open_memmap(
        temporary_vectors,
        mode="w+",
        dtype=np.float32,
        shape=(len(merged), dimension),
    )
    combined[: len(baseline)] = vectors
    combined[len(baseline) :] = new_vectors
    combined.flush()
    del combined
    os.replace(temporary_vectors, vector_target)

    index.add(new_vectors)
    temporary_index = output / (FAISS_FILE + ".tmp")
    faiss.write_index(index, str(temporary_index))
    os.replace(temporary_index, output / FAISS_FILE)
    _atomic_json(output / CHUNKS_FILE, merged)
    _atomic_json(output / CHUNK_IDS_FILE, [chunk["chunk_id"] for chunk in merged])

    staged_manifest = {
        **manifest,
        "chunk_count": len(merged),
        "chunks_sha256": file_sha256(merged_chunks_file),
    }
    _atomic_json(output / MANIFEST_FILE, staged_manifest)
    return {
        **staged_manifest,
        "baseline_chunk_count": len(baseline),
        "added_chunk_count": len(additions),
        "reused_vector_rows": len(baseline),
        "encoded_vector_rows": len(additions),
        "faiss_ntotal": int(index.ntotal),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-chunks", default="data/chunks.jsonl")
    parser.add_argument("--merged-chunks", required=True)
    parser.add_argument("--baseline-artifacts", default="artifacts")
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-path", default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    result = extend_index(
        args.baseline_chunks,
        args.merged_chunks,
        args.baseline_artifacts,
        args.output,
        model_path=args.model_path,
        device=args.device,
        batch_size=args.batch_size,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
