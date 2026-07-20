"""对增量知识块做向量化(与后端完全一致:bge-large-zh-v1.5 + 行归一化)。

必须用后端的 venv 运行(那里有 sentence-transformers 与模型缓存):
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    "<backend>/.venv/bin/python" embed_chunks.py [--date YYYY-MM-DD]

输出 output/<日期>/vectors.npy,行序与 chunks.jsonl 一致。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).parent
MODEL_NAME = "BAAI/bge-large-zh-v1.5"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()

    day_dir = BASE_DIR / "output" / args.date
    chunks_path = day_dir / "chunks.jsonl"
    if not chunks_path.is_file():
        print(f"没有找到 {chunks_path},先运行 build_chunks.py", file=sys.stderr)
        return 1

    texts = []
    with chunks_path.open(encoding="utf-8") as handle:
        for line in handle:
            texts.append(json.loads(line)["text"])
    if not texts:
        print("没有需要向量化的块,跳过。")
        return 0

    # 默认离线,避免定时任务里意外联网;缓存已在本机。
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    from sentence_transformers import SentenceTransformer

    print(f"加载 {MODEL_NAME} …")
    model = SentenceTransformer(MODEL_NAME)
    vectors = model.encode(
        texts,
        batch_size=16,
        show_progress_bar=True,
        convert_to_numpy=True,
    ).astype(np.float32)

    # 与后端 retrieval.index.normalize_rows 等价:内积索引需要单位向量。
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vectors = vectors / norms

    output = day_dir / "vectors.npy"
    np.save(output, vectors, allow_pickle=False)
    print(f"向量化 {vectors.shape[0]} 块 (dim={vectors.shape[1]}) -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
