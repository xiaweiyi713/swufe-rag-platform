"""Unified mixed-dialogue orchestration above the frozen B/C contracts."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import re
from threading import Lock, RLock
import time
from typing import Any, Callable

from academic_audit.structured_qa import (
    answer_structured_curriculum,
    scope_clarification as structured_scope_clarification,
)
from contracts import (
    AnswerResult,
    CHUNK_FIELDS,
    CitationValidationError,
    KnowledgeChunk,
    RetrievedChunk,
)
from generation.general_chat import GeneralChatService
from generation.grounded_answer import TrustedAnswerBinder
from generation.prompts import REFUSAL_TEXT
from storage.metadata_db import MetadataDB, OfficialLink
from swufe_rag.routing.router import HybridRouter
from swufe_rag.routing.schemas import RouteContext, RouteDecision, RouteMode


SchoolRetrieveFunction = Callable[..., list[RetrievedChunk]]
SchoolAnswerFunction = Callable[[str, list[dict[str, Any]]], AnswerResult]

SCHOOL_NOT_FOUND_TEXT = (
    "当前知识库中未找到能够明确回答该问题的西南财大官方规定。"
    "我不会改用通用模型猜测学校事实；请查看下方已登记的官方来源，"
    "或咨询教务处、学院教务办。"
)


@dataclass
class SessionState:
    last_mode: RouteMode | None = None
    last_intent: str | None = None
    last_college: str | None = None
    last_cohort: str | None = None
    last_rewritten_query: str | None = None
    recent_messages: list[str] = field(default_factory=list)
    general_history: list[tuple[str, str]] = field(default_factory=list)
    # Typed query context lives in the same session record as route/general
    # memory so Redis-backed sessions remain complete across processes.
    last_normalized_query: dict[str, Any] | None = None
    pending_normalized_query: dict[str, Any] | None = None
    context_question: str | None = None

    def route_context(self) -> RouteContext:
        return RouteContext(
            last_mode=self.last_mode,
            last_intent=self.last_intent,
            last_college=self.last_college,
            last_cohort=self.last_cohort,
            last_rewritten_query=self.last_rewritten_query,
            recent_messages=tuple(self.recent_messages[-8:]),
        )

    def record_route(self, question: str, decision: RouteDecision) -> None:
        self.last_mode = decision.mode
        self.last_intent = decision.intent
        self.last_college = decision.college
        self.last_cohort = decision.cohort
        self.last_rewritten_query = decision.rewritten_query
        self.recent_messages.append(question)
        del self.recent_messages[:-16]


class InMemorySessionStore:
    def __init__(self, *, max_sessions: int = 2000) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be positive")
        self.max_sessions = max_sessions
        self._states: dict[str, SessionState] = {}
        self._order: list[str] = []
        self._lock = RLock()
        # Starlette may resume a synchronous streaming generator on a different
        # worker thread. Plain locks can be released by that worker; RLocks are
        # thread-owned and would leak a stripe in that situation.
        self._guards = tuple(Lock() for _ in range(256))

    @contextmanager
    def guard(self, session_id: str | None):
        if session_id is None:
            yield
            return
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be null or a non-empty string")
        lock = self._guards[hash(session_id.strip()) % len(self._guards)]
        with lock:
            yield

    def get(self, session_id: str | None) -> SessionState:
        if session_id is None:
            return SessionState()
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be null or a non-empty string")
        key = session_id.strip()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                if len(self._states) >= self.max_sessions:
                    oldest = self._order.pop(0)
                    self._states.pop(oldest, None)
                state = SessionState()
                self._states[key] = state
                self._order.append(key)
            return state


def _summary(chunk: RetrievedChunk) -> dict[str, Any]:
    return {
        "chunk_id": chunk["chunk_id"],
        "doc_title": chunk["doc_title"],
        "article": chunk["article"],
        "college": chunk["college"],
        "cohort": chunk["cohort"],
        "score": chunk["score"],
        "is_table": bool(chunk["is_table"]),
        "summary": chunk["text"][:260],
    }


def _link(link: OfficialLink) -> dict[str, str]:
    return {
        "source_id": link.source_id,
        "title": link.title,
        "page_url": link.page_url,
        "file_url": link.file_url,
    }


def _source_appendix(citations: list[dict[str, Any]]) -> str:
    if not citations:
        return ""

    groups: list[dict[str, Any]] = []
    group_indexes: dict[tuple[str, ...], int] = {}
    for citation in citations:
        doc_title = str(citation.get("doc_title") or "未命名来源")
        page_url = str(citation.get("page_url") or "").strip()
        file_url = str(citation.get("file_url") or "").strip()
        key = (
            ("file", file_url)
            if file_url
            else ("source", doc_title, page_url)
        )
        if key not in group_indexes:
            group_indexes[key] = len(groups)
            groups.append({
                "doc_title": doc_title,
                "file_url": file_url,
                "markers": [],
                "pages": {},
                "unpaged_url": page_url,
            })
        group = groups[group_indexes[key]]
        marker = int(citation["marker"])
        if marker not in group["markers"]:
            group["markers"].append(marker)

        article = str(citation.get("article") or "")
        physical_page = citation.get("physical_page")
        try:
            page_number = int(physical_page) if physical_page is not None else None
        except (TypeError, ValueError):
            page_number = None
        if page_number is None:
            match = re.search(r"(?:原文件)?第(\d+)页", article)
            if match is None:
                match = re.search(r"(?:#|[?&])page=(\d+)", page_url)
            page_number = int(match.group(1)) if match else None
        if page_number is not None:
            group["pages"].setdefault(page_number, page_url)

    lines = ["来源文件与页码："]
    for group in groups:
        pages = sorted(group["pages"])
        page_urls = {
            group["pages"][page]
            for page in pages
            if group["pages"][page]
        }
        if pages and len(page_urls) <= 1:
            page = f"原文件第{'、'.join(str(value) for value in pages)}页"
            page_url = next(iter(page_urls), "")
            page_link = f"[{page}]({page_url})" if page_url else page
        elif pages:
            page_parts = []
            for page_number in pages:
                label = f"原文件第{page_number}页"
                page_url = group["pages"][page_number]
                page_parts.append(
                    f"[{label}]({page_url})" if page_url else label
                )
            page_link = "、".join(page_parts)
        else:
            page = "页码未标注"
            page_url = group["unpaged_url"]
            page_link = f"[{page}]({page_url})" if page_url else page
        markers = "".join(f"[{marker}]" for marker in group["markers"])
        file_url = group["file_url"]
        file_link = (
            f"[下载原文件]({file_url})"
            if file_url
            else "原文件链接未登记"
        )
        lines.append(
            f"- {markers}《{group['doc_title']}》，{page_link} · {file_link}"
        )
    return "\n\n" + "\n".join(lines)


class HybridRuntime:
    """Routes first, then invokes exactly one isolated answer branch."""

    def __init__(
        self,
        *,
        router: HybridRouter,
        school_retrieve: SchoolRetrieveFunction,
        school_answer: SchoolAnswerFunction,
        general_chat: GeneralChatService,
        metadata_db: MetadataDB,
        sessions: InMemorySessionStore | None = None,
        runtime_mode: str = "production-hybrid",
        runtime_info: dict[str, Any] | None = None,
    ) -> None:
        self.router = router
        self.school_retrieve = school_retrieve
        self.school_answer = school_answer
        self.general_chat = general_chat
        self.metadata_db = metadata_db
        self.binder = TrustedAnswerBinder(metadata_db)
        self.sessions = sessions or InMemorySessionStore()
        self.mode = runtime_mode
        self.runtime_info = dict(runtime_info or {})

    @staticmethod
    def _clarification(decision: RouteDecision, text: str) -> dict[str, Any]:
        return {
            "mode": "school_rag",
            "answer_md": text,
            "citations": [],
            "retrieved": [],
            "official_links": [],
            "refused": False,
            "route": decision,
        }

    def _scope_clarification(
        self, decision: RouteDecision
    ) -> dict[str, Any] | None:
        known_colleges = self.metadata_db.known_colleges()
        known_cohorts = self.metadata_db.known_cohorts()
        if decision.college and decision.college not in known_colleges:
            return self._clarification(
                decision,
                "当前可信来源中没有这个学院的登记记录。请确认学院全称后再问。",
            )
        if decision.cohort and decision.cohort not in known_cohorts:
            return self._clarification(
                decision,
                f"当前可信来源中没有 {decision.cohort} 级对应材料，请确认入学年级。",
            )
        # School-wide curriculum principles are intentionally published without
        # a college scope. Major-specific questions are mapped to a college by
        # the router, so only promotion rules require an explicit college.
        needs_college = decision.intent == "promotion"
        if needs_college and not decision.college:
            return self._clarification(
                decision,
                "请先告诉我你的学院。培养方案和推免细则可能因学院而不同。",
            )
        needs_cohort = decision.intent in {
            "curriculum",
            "course_selection",
            "promotion",
        }
        if needs_cohort and not decision.cohort:
            return self._clarification(
                decision,
                "请问你是哪一入学年级？不同年级的培养方案和课程设置可能不同。",
            )
        return None

    def _insufficient(
        self,
        decision: RouteDecision,
        *,
        retrieved: list[RetrievedChunk],
    ) -> dict[str, Any]:
        topic = None if decision.intent == "school_general" else decision.intent
        links = self.metadata_db.official_links(
            college=decision.college,
            cohort=decision.cohort,
            topic=topic,
            policy_year=decision.policy_year,
            limit=3,
        )
        return {
            "mode": "school_rag",
            "answer_md": SCHOOL_NOT_FOUND_TEXT,
            "citations": [],
            "retrieved": [_summary(chunk) for chunk in retrieved],
            "official_links": [_link(link) for link in links],
            "refused": True,
            "route": decision,
        }

    def _general(
        self,
        question: str,
        decision: RouteDecision,
        state: SessionState,
        *,
        web_context: str | None = None,
    ) -> dict[str, Any]:
        if web_context:
            text = self.general_chat.answer(
                question, state.general_history, web_context=web_context
            )
        else:
            text = self.general_chat.answer(question, state.general_history)
        state.general_history.extend([("user", question), ("assistant", text)])
        del state.general_history[:-24]
        return {
            "mode": "general_chat",
            "answer_md": text,
            "citations": [],
            "retrieved": [],
            "official_links": [],
            "refused": False,
            "route": decision,
        }

    def handle_question(
        self,
        question: str,
        *,
        college: str | None = None,
        cohort: str | None = None,
        session_id: str | None = None,
        top_k: int = 8,
        include_route_debug: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        state = self.sessions.get(session_id)
        decision = self.router.route(
            question,
            context=state.route_context(),
            college=college,
            cohort=cohort,
        )
        if decision.mode == "general_chat":
            payload = self._general(question, decision, state)
        else:
            structured_issue = structured_scope_clarification(
                question, cohort=decision.cohort
            )
            clarification = (
                self._clarification(decision, structured_issue)
                if structured_issue
                else self._scope_clarification(decision)
            )
            if clarification is not None:
                payload = clarification
            else:
                structured = answer_structured_curriculum(
                    question,
                    cohort=decision.cohort,
                    metadata_db=self.metadata_db,
                )
                retrieval_query = " ".join(
                    dict.fromkeys(
                        [decision.rewritten_query, *decision.search_terms]
                    )
                )
                if structured is not None:
                    answer, chunks = structured
                    payload = {
                        "mode": "school_rag",
                        **answer,
                        "retrieved": [_summary(chunk) for chunk in chunks],
                        "official_links": [],
                        "route": decision,
                    }
                    chunks = []
                else:
                    chunks = self.school_retrieve(
                    retrieval_query,
                    top_k=top_k,
                    college=decision.college,
                    cohort=decision.cohort,
                    policy_year=decision.policy_year,
                    topic=(
                        None
                        if decision.intent == "school_general"
                        else decision.intent
                    ),
                    )
                if structured is None and not chunks:
                    payload = self._insufficient(decision, retrieved=[])
                elif structured is None:
                    raw = self.school_answer(decision.rewritten_query, chunks)
                    if raw["refused"] or raw["answer_md"] == REFUSAL_TEXT:
                        payload = self._insufficient(decision, retrieved=chunks)
                    else:
                        try:
                            answer = self.binder.bind(raw, chunks)
                        except CitationValidationError:
                            payload = self._insufficient(decision, retrieved=chunks)
                        else:
                            payload = {
                                "mode": "school_rag",
                                **answer,
                                "retrieved": [_summary(chunk) for chunk in chunks],
                                "official_links": [],
                                "route": decision,
                            }
        state.record_route(question, decision)
        payload["latency_ms"] = round(
            (time.perf_counter() - started) * 1000, 2
        )
        if include_route_debug:
            payload["route"] = decision.to_dict()
        else:
            payload.pop("route", None)
        if (
            payload.get("mode") == "school_rag"
            and not payload.get("refused")
            and payload.get("citations")
        ):
            payload["answer_md"] += _source_appendix(payload["citations"])
        return payload

    def ask(
        self,
        question: str,
        *,
        college: str | None = None,
        cohort: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return self.handle_question(
            question,
            college=college,
            cohort=cohort,
            session_id=session_id,
        )

    def debug_ask(self, question: str, **kwargs: Any) -> dict[str, Any]:
        return self.handle_question(question, include_route_debug=True, **kwargs)

    def source(self, chunk_id: str) -> KnowledgeChunk | None:
        stored = self.metadata_db.chunk(chunk_id)
        if stored is None:
            return None
        values = {
            "chunk_id": stored.chunk_id,
            "text": stored.text,
            "doc_title": stored.doc_title,
            "article": stored.article,
            "level": stored.level,
            "college": stored.college,
            "cohort": stored.cohort,
            "year": stored.year,
            "status": stored.status,
            "page_url": stored.page_url,
            "file_url": stored.file_url,
            "is_table": stored.is_table,
        }
        return {key: values[key] for key in CHUNK_FIELDS}  # type: ignore[return-value]

    def options(self) -> dict[str, Any]:
        report = self.metadata_db.integrity_report()
        return {
            "mode": self.mode,
            "colleges": list(self.metadata_db.known_colleges()),
            "cohorts": list(self.metadata_db.known_cohorts()),
            "chunk_count": report["chunks"],
            "default_top_k": 8,
            "runtime": getattr(self, "runtime_info", {}),
        }


def handle_question(runtime: HybridRuntime, question: str, **kwargs: Any) -> dict[str, Any]:
    return runtime.handle_question(question, **kwargs)


__all__ = [
    "HybridRuntime",
    "InMemorySessionStore",
    "SCHOOL_NOT_FOUND_TEXT",
    "SessionState",
    "handle_question",
]
