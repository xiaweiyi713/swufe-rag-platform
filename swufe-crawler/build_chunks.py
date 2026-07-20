"""把爬取的文章转成 swufe-rag 契约 1 知识块。

读 output/<日期>/articles.jsonl,按段落聚合切块(每块 ~max_len 字),
输出 output/<日期>/chunks.jsonl。块格式与后端现有 60k+ 块完全对齐:
text 首行为“《标题》条款行”,元数据为 校级/全校/不限。

用法:
    .venv/bin/python build_chunks.py [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).parent

TOPIC_LABELS = {
    "notice": "通知公告",
    "campus_news": "校园新闻",
}


def split_paragraphs(text: str, max_len: int) -> list[str]:
    """段落聚合切块:相邻段落合并到不超过 max_len,超长段落硬切。"""
    blocks: list[str] = []
    current = ""
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while len(paragraph) > max_len:
            head, paragraph = paragraph[:max_len], paragraph[max_len:]
            if current:
                blocks.append(current)
                current = ""
            blocks.append(head)
        if len(current) + len(paragraph) + 1 > max_len and current:
            blocks.append(current)
            current = paragraph
        else:
            current = f"{current}\n{paragraph}" if current else paragraph
    if current:
        blocks.append(current)
    return blocks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--config", default=str(BASE_DIR / "config.yaml"))
    args = parser.parse_args()

    with Path(args.config).open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    max_len = int(config.get("chunk", {}).get("max_len", 460))

    day_dir = BASE_DIR / "output" / args.date
    articles_path = day_dir / "articles.jsonl"
    if not articles_path.is_file():
        print(f"没有找到 {articles_path},先运行 crawler.py", file=sys.stderr)
        return 1

    chunks_path = day_dir / "chunks.jsonl"
    total = 0
    with articles_path.open(encoding="utf-8") as source, \
            chunks_path.open("w", encoding="utf-8") as sink:
        for line in source:
            article = json.loads(line)
            url = article["url"]
            title = article["title"]
            published = article.get("published", args.date)
            label = TOPIC_LABELS.get(article.get("topic", ""), "官网信息")
            article_line = f"{label} / 发布于 {published}"
            year = int(published[:4]) if published[:4].isdigit() else date.today().year
            file_url = (article.get("attachments") or [url])[0]
            doc_hash = hashlib.sha1(url.encode()).hexdigest()[:12]

            for index, block in enumerate(split_paragraphs(article["text"], max_len), start=1):
                chunk = {
                    "chunk_id": f"web_{doc_hash}_{index:04d}",
                    "text": f"《{title}》{article_line}\n{block}",
                    "doc_title": title,
                    "article": article_line,
                    "college": "全校",
                    "level": "校级",
                    "cohort": "不限",
                    "year": year,
                    "status": "现行",
                    "page_url": url,
                    "file_url": file_url,
                    "is_table": False,
                    # 注意:后端 load_chunks 严格校验字段(多一个都报 ContractError),
                    # 这里只能是契约 1 的 12 个字段;topic 由 merge 从 articles.jsonl 读取。
                }
                sink.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                total += 1

    print(f"生成 {total} 个知识块 -> {chunks_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
