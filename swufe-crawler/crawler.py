"""西南财经大学官网增量爬虫。

从配置的列表页发现文章链接(高校博达 CMS 的 info/<栏目>/<文章>.htm 形态),
只抓 state.sqlite 里没见过的新文章,限速抓取详情页并抽取标题/日期/正文/附件,
输出到 output/<日期>/articles.jsonl 供 build_chunks.py 使用。

用法:
    .venv/bin/python crawler.py [--config config.yaml] [--max-per-site N]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
ARTICLE_LINK = re.compile(r"info/\d+/\d+\.htm$")
DATE_PATTERNS = [
    re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})"),
    re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日?"),
]
ATTACHMENT_SUFFIXES = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip")


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def open_state(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS crawled (
            url TEXT PRIMARY KEY,
            title TEXT,
            published TEXT,
            fetched_at TEXT NOT NULL
        )"""
    )
    return connection


def fetch(session: requests.Session, url: str, timeout: float) -> str | None:
    try:
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [跳过] {url} 请求失败: {exc}", file=sys.stderr)
        return None
    if response.encoding in (None, "ISO-8859-1"):
        response.encoding = response.apparent_encoding
    return response.text


def discover_articles(list_html: str, list_url: str) -> list[tuple[str, str | None]]:
    """列表页里的 (文章链接, 发布日期),按出现顺序去重(新文章通常在最前)。

    博达 CMS 的列表条目形如
        <li><a href="info/1025/35531.htm">标题</a><span>[2024年06月19日]</span></li>
    条目里的日期是权威发布日期;详情页正文可能出现其它年份(如政策适用范围
    “2008年9月1日以后…”),扫全文取首个日期会误判,因此优先用列表页日期。
    """
    soup = BeautifulSoup(list_html, "lxml")
    seen: dict[str, str | None] = {}
    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(list_url, anchor["href"])
        # 只收列表页同域的文章,避免爬出学校站点
        if urlparse(absolute).netloc != urlparse(list_url).netloc:
            continue
        if not ARTICLE_LINK.search(urlparse(absolute).path):
            continue
        url = absolute.split("#")[0]
        if url in seen:
            continue
        seen[url] = _entry_date(anchor)
    return list(seen.items())


def _entry_date(anchor) -> str | None:
    """从列表条目(链接所在的 li/行)里取发布日期。"""
    container = anchor.find_parent(["li", "tr", "div"]) or anchor.parent
    if container is None:
        return None
    text = container.get_text(" ", strip=True)
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            year, month, day = (int(part) for part in match.groups())
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                continue
    return None


def extract_date(html: str) -> str | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(html)
        if match:
            year, month, day = (int(part) for part in match.groups())
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                continue
    return None


def extract_article(html: str, url: str, listed_date: str | None = None) -> dict | None:
    soup = BeautifulSoup(html, "lxml")

    title = ""
    if soup.title and soup.title.string:
        # 博达 CMS 的 <title> 形如 “停电通知-西南财经大学”
        title = re.sub(r"[-—_|]\s*西南财经大学.*$", "", soup.title.string).strip()
    if not title:
        h1 = soup.find(["h1", "h2"])
        title = h1.get_text(strip=True) if h1 else ""
    if not title:
        return None

    body = soup.find(class_="v_news_content")
    if body is None:
        body = soup.find(
            class_=re.compile(r"(article|news)?[-_]?content|zhengwen|wzzw", re.I)
        )
    if body is None:
        return None

    attachments = []
    for anchor in body.find_all("a", href=True):
        absolute = urljoin(url, anchor["href"])
        if absolute.lower().endswith(ATTACHMENT_SUFFIXES):
            attachments.append(absolute)

    paragraphs = [
        line.strip()
        for line in body.get_text("\n").splitlines()
        if line.strip()
    ]
    text = "\n".join(paragraphs)
    if len(text) < 30:  # 纯图片/空通知没有检索价值
        return None

    return {
        "url": url,
        "title": title,
        # 列表页日期最可靠;没有时才退回详情页启发式。
        "published": listed_date or extract_date(html) or date.today().isoformat(),
        "text": text,
        "attachments": attachments,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(BASE_DIR / "config.yaml"))
    parser.add_argument("--max-per-site", type=int, default=None)
    args = parser.parse_args()

    config = load_config(Path(args.config))
    crawl = config.get("crawl", {})
    delay = float(crawl.get("delay_seconds", 1.5))
    timeout = float(crawl.get("timeout_seconds", 20))
    max_new = args.max_per_site or int(crawl.get("max_new_per_site", 15))

    state_dir = BASE_DIR / "state"
    state_dir.mkdir(exist_ok=True)
    state = open_state(state_dir / "state.sqlite")

    output_dir = BASE_DIR / "output" / date.today().isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "articles.jsonl"

    session = requests.Session()
    session.headers["User-Agent"] = crawl.get("user_agent", "swufe-rag-crawler/1.0")

    total_new = 0
    with output_path.open("w", encoding="utf-8") as sink:
        for site in config.get("sites", []):
            name, list_url, topic = site["name"], site["list_url"], site.get("topic", "notice")
            print(f"[站点] {name} <- {list_url}")
            list_html = fetch(session, list_url, timeout)
            if list_html is None:
                continue
            candidates = discover_articles(list_html, list_url)
            print(f"  列表页发现 {len(candidates)} 个文章链接")

            site_new = 0
            for url, listed_date in candidates:
                if site_new >= max_new:
                    break
                already = state.execute(
                    "SELECT 1 FROM crawled WHERE url = ?", (url,)
                ).fetchone()
                if already:
                    continue
                time.sleep(delay)
                html = fetch(session, url, timeout)
                if html is None:
                    continue
                article = extract_article(html, url, listed_date)
                if article is None:
                    print(f"  [跳过] 无法抽取正文: {url}")
                    # 记录避免每天重试无正文页面
                    state.execute(
                        "INSERT OR IGNORE INTO crawled (url, title, published, fetched_at) VALUES (?, ?, ?, ?)",
                        (url, "", "", datetime.now().isoformat(timespec="seconds")),
                    )
                    continue
                article.update({"site": name, "topic": topic})
                sink.write(json.dumps(article, ensure_ascii=False) + "\n")
                state.execute(
                    "INSERT OR IGNORE INTO crawled (url, title, published, fetched_at) VALUES (?, ?, ?, ?)",
                    (url, article["title"], article["published"],
                     datetime.now().isoformat(timespec="seconds")),
                )
                site_new += 1
                total_new += 1
                print(f"  [新增] {article['published']} {article['title']}")
            state.commit()

    print(f"共抓取 {total_new} 篇新文章 -> {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
