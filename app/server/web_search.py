"""Small, keyless web-search adapter used by the optional search mode.

The adapter deliberately returns snippets and links only. Web pages are
untrusted context and are never mixed into the school's citation ledger.
"""

from __future__ import annotations

from html.parser import HTMLParser
import os
from urllib.parse import parse_qs, unquote, urlparse


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "a" and "result__a" in classes and values.get("href"):
            self._current = {"url": self._direct_url(values["href"] or "")}
            self._capture = "title"
        elif self.results and "result__snippet" in classes:
            self._current = self.results[-1]
            self._capture = "snippet"

    def handle_data(self, data: str) -> None:
        if self._current is None or self._capture is None:
            return
        self._current[self._capture] = (
            self._current.get(self._capture, "") + " " + data
        ).strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture == "title" and self._current is not None:
            self.results.append(self._current)
            self._current = None
            self._capture = None
        elif tag in {"a", "div"} and self._capture == "snippet":
            self._current = None
            self._capture = None

    @staticmethod
    def _direct_url(value: str) -> str:
        parsed = urlparse(value)
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target) if target else value


def search_web(query: str, *, limit: int = 5) -> list[dict[str, str]]:
    """Search a public HTML endpoint without requiring another API key."""
    clean = query.strip()
    if not clean:
        return []
    try:
        import httpx

        endpoint = os.getenv(
            "SWUFE_RAG_WEB_SEARCH_ENDPOINT",
            "https://html.duckduckgo.com/html/",
        )
        response = httpx.get(
            endpoint,
            params={"q": clean},
            headers={"User-Agent": "SwufeAsk/1.0 (web search)"},
            timeout=8.0,
            trust_env=False,
        )
        response.raise_for_status()
        parser = _DuckDuckGoParser()
        parser.feed(response.text)
    except Exception:
        return []

    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parser.results:
        url = item.get("url", "").strip()
        title = item.get("title", "").strip()
        snippet = item.get("snippet", "").strip()
        if not url or not title or url in seen:
            continue
        seen.add(url)
        output.append({
            "title": title[:180],
            "url": url[:1000],
            "snippet": snippet[:600],
        })
        if len(output) >= max(1, min(limit, 8)):
            break
    return output


def format_web_context(sources: list[dict[str, str]]) -> str | None:
    if not sources:
        return None
    lines = [
        "【联网搜索资料】以下内容来自公开网页，仅用于辅助回答当前问题；如与学校官方文件冲突，以官方文件为准。"
    ]
    for index, source in enumerate(sources, start=1):
        lines.append(
            f"[{index}] {source.get('title', '')}\n"
            f"链接：{source.get('url', '')}\n"
            f"摘要：{source.get('snippet', '')}"
        )
    return "\n\n".join(lines)


__all__ = ["format_web_context", "search_web"]
