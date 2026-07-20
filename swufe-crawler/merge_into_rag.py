"""把增量知识块安全合并进 swufe-rag 后端知识库。

后端启动时做强一致性校验(chunks.jsonl 的 sha256、块数、chunk_id 顺序、
FAISS ntotal 必须全部对齐),因此合并按同一契约操作:

    1. 备份 chunks.jsonl / sources.csv / metadata.sqlite3 / artifacts 全部文件
    2. 过滤已存在的 chunk_id(幂等,可重复运行)
    3. 追加 artifacts: vectors.npy / chunks.json / chunk_ids.json / index.faiss
    4. 追加 data/chunks.jsonl
    5. 登记 data/sources.csv:后端启动会按 CSV+chunks 重建 metadata,并要求
       每个块的 (doc_title, level, college, cohort, year, status, file_url)
       七元组能精确命中一行登记,否则拒绝启动(trusted source 校验)
    6. 最后重写 manifest.json(chunk_count + chunks_sha256)作为提交标记
    7. 全量自检,不一致立即报错(此时可用 --rollback 恢复)

默认 dry-run 只打印计划;确认无误后加 --apply 执行。
必须用后端 venv 运行(需要 faiss/numpy):
    "<backend>/.venv/bin/python" merge_into_rag.py [--date YYYY-MM-DD] [--apply]
    "<backend>/.venv/bin/python" merge_into_rag.py --rollback backup/<时间戳>

合并后需重启后端服务(python -m app.server)才会加载新块。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import yaml

BASE_DIR = Path(__file__).parent

BACKUP_ITEMS = [
    ("data/chunks.jsonl", "chunks.jsonl"),
    ("data/sources.csv", "sources.csv"),
    ("data/metadata.sqlite3", "metadata.sqlite3"),
    ("artifacts/manifest.json", "manifest.json"),
    ("artifacts/vectors.npy", "vectors.npy"),
    ("artifacts/chunks.json", "chunks.json"),
    ("artifacts/chunk_ids.json", "chunk_ids.json"),
    ("artifacts/index.faiss", "index.faiss"),
]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def persist_raw_articles(backend: Path, articles: dict[str, dict]) -> int:
    """Persist the canonical text needed for a future full-corpus rebuild."""

    created = 0
    for url, article in articles.items():
        text = str(article.get("text", "")).strip()
        if not text:
            raise ValueError(f"缺少可落盘的网页原文: {url}")
        source_key = "web/" + hashlib.sha1(url.encode()).hexdigest()[:12] + ".md"
        raw_path = backend / "data/raw" / source_key
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_path.is_file():
            continue
        raw_path.write_text(text + "\n", encoding="utf-8")
        created += 1
    return created


def rollback(backend: Path, backup_dir: Path) -> int:
    print(f"从 {backup_dir} 回滚后端数据…")
    for relative, name in BACKUP_ITEMS:
        source = backup_dir / name
        if source.is_file():
            shutil.copy2(source, backend / relative)
            print(f"  恢复 {relative}")
    print("回滚完成。请重启后端服务。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--config", default=str(BASE_DIR / "config.yaml"))
    parser.add_argument("--apply", action="store_true", help="真正执行合并(默认 dry-run)")
    parser.add_argument("--rollback", metavar="BACKUP_DIR", help="从备份目录恢复后端数据")
    args = parser.parse_args()

    with Path(args.config).open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    backend = Path(config["backend_dir"])

    if args.rollback:
        return rollback(backend, Path(args.rollback))

    day_dir = BASE_DIR / "output" / args.date
    inc_chunks_path = day_dir / "chunks.jsonl"
    inc_vectors_path = day_dir / "vectors.npy"
    if not inc_chunks_path.is_file() or not inc_vectors_path.is_file():
        print(f"{day_dir} 缺少 chunks.jsonl / vectors.npy,先跑前序步骤", file=sys.stderr)
        return 1

    increments = load_jsonl(inc_chunks_path)
    vectors = np.load(inc_vectors_path, allow_pickle=False)
    if len(increments) != vectors.shape[0]:
        print(f"块数({len(increments)})与向量行数({vectors.shape[0]})不一致", file=sys.stderr)
        return 1

    # topic 映射(chunks.jsonl 只能有契约字段,topic 从 articles.jsonl 取)
    topic_by_url: dict[str, str] = {}
    article_by_url: dict[str, dict] = {}
    articles_path = day_dir / "articles.jsonl"
    if articles_path.is_file():
        for article in load_jsonl(articles_path):
            topic_by_url[article["url"]] = article.get("topic", "notice")
            article_by_url[article["url"]] = article

    artifacts = backend / "artifacts"
    existing_ids = set(json.loads((artifacts / "chunk_ids.json").read_text(encoding="utf-8")))

    keep_rows = [i for i, c in enumerate(increments) if c["chunk_id"] not in existing_ids]
    skipped = len(increments) - len(keep_rows)
    new_chunks = [increments[i] for i in keep_rows]
    new_vectors = vectors[keep_rows]

    print(f"增量 {len(increments)} 块;已存在跳过 {skipped};待合并 {len(new_chunks)} 块")
    if not new_chunks:
        if args.apply:
            restored = persist_raw_articles(backend, article_by_url)
            print(f"补齐 data/raw 网页原文 {restored} 篇")
        print("没有新块需要合并。")
        return 0
    documents = {c["page_url"]: c["doc_title"] for c in new_chunks}
    for url, title in list(documents.items())[:10]:
        print(f"  + [{topic_by_url.get(url, 'notice')}] {title}")

    if not args.apply:
        print("\n(dry-run)确认无误后加 --apply 执行合并。")
        return 0

    # ---- 1. 备份 ----
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = BASE_DIR / "backup" / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for relative, name in BACKUP_ITEMS:
        shutil.copy2(backend / relative, backup_dir / name)
    print(f"已备份后端数据 -> {backup_dir}")

    try:
        persisted_raw = persist_raw_articles(backend, article_by_url)
        print(f"data/raw 新增网页原文 {persisted_raw} 篇")

        # ---- 2. artifacts 追加 ----
        base_vectors = np.load(artifacts / "vectors.npy", allow_pickle=False)
        merged_vectors = np.concatenate(
            [base_vectors, new_vectors.astype(base_vectors.dtype)], axis=0
        )
        np.save(artifacts / "vectors.npy.tmp.npy", merged_vectors, allow_pickle=False)
        (artifacts / "vectors.npy.tmp.npy").replace(artifacts / "vectors.npy")

        chunk_dicts = json.loads((artifacts / "chunks.json").read_text(encoding="utf-8"))
        contract_rows = [{k: v for k, v in c.items()} for c in new_chunks]
        chunk_dicts.extend(contract_rows)
        (artifacts / "chunks.json").write_text(
            json.dumps(chunk_dicts, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        chunk_ids = json.loads((artifacts / "chunk_ids.json").read_text(encoding="utf-8"))
        base_count = len(chunk_ids)
        chunk_ids.extend(c["chunk_id"] for c in new_chunks)
        (artifacts / "chunk_ids.json").write_text(
            json.dumps(chunk_ids, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        import faiss

        index = faiss.read_index(str(artifacts / "index.faiss"))
        index.add(new_vectors.astype(np.float32))
        faiss.write_index(index, str(artifacts / "index.faiss"))

        # ---- 3. chunks.jsonl 追加 ----
        with (backend / "data/chunks.jsonl").open("a", encoding="utf-8") as sink:
            for chunk in new_chunks:
                sink.write(json.dumps(chunk, ensure_ascii=False) + "\n")

        # ---- 4. 登记 data/sources.csv ----
        # 后端启动时按 CSV + chunks 自动重建 metadata.sqlite3;每篇文章登记一行,
        # 七元组必须与该文章所有块完全一致(file 列作为 source_key,须唯一)。
        registry_path = backend / "data/sources.csv"
        with registry_path.open(encoding="utf-8", newline="") as handle:
            registered = {(row["doc_title"], row["file_url"]) for row in csv.DictReader(handle)}
        documents_by_url: dict[str, dict] = {}
        for chunk in new_chunks:
            documents_by_url.setdefault(chunk["page_url"], chunk)
        added_sources = 0
        with registry_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            for url, chunk in documents_by_url.items():
                source_key = "web/" + hashlib.sha1(url.encode()).hexdigest()[:12] + ".md"
                article = article_by_url.get(url)
                if article is None or not str(article.get("text", "")).strip():
                    raise ValueError(f"缺少可落盘的网页原文: {url}")
                if (chunk["doc_title"], chunk["file_url"]) in registered:
                    continue
                # file 列有后缀白名单(.pdf/.docx/.txt/.md),网页内容登记为 .md
                writer.writerow([
                    source_key, chunk["doc_title"], chunk["level"], chunk["college"],
                    chunk["cohort"], chunk["year"], chunk["status"],
                    chunk["page_url"], chunk["file_url"],
                    date.today().isoformat(),
                ])
                added_sources += 1
        print(f"sources.csv 登记 {added_sources} 个新来源")

        # ---- 5. manifest 最后提交 ----
        manifest = json.loads((artifacts / "manifest.json").read_text(encoding="utf-8"))
        manifest["chunk_count"] = len(chunk_ids)
        manifest["chunks_sha256"] = file_sha256(backend / "data/chunks.jsonl")
        (artifacts / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # ---- 6. 自检 ----
        assert index.ntotal == len(chunk_ids) == len(chunk_dicts) == merged_vectors.shape[0], \
            "合并后计数不一致"
        jsonl_count = sum(1 for _ in (backend / "data/chunks.jsonl").open(encoding="utf-8"))
        assert jsonl_count == len(chunk_ids), "chunks.jsonl 行数与 chunk_ids 不一致"
    except Exception:
        print("\n合并失败!用以下命令回滚:", file=sys.stderr)
        print(f'  merge_into_rag.py --rollback "{backup_dir}"', file=sys.stderr)
        raise

    print(f"合并完成:{base_count} -> {len(chunk_ids)} 块(+{len(new_chunks)})")
    print("请重启后端服务加载新知识库(python -m app.server)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
