"""Canonical V16 orchestration over typed plans and evidence packets."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from queue import Full, Queue
import re
from threading import Event, Thread
import time
from typing import Any

from academic_audit.database import AcademicDatabase
from academic_audit.course_subjects import clean_course_name
from academic_audit.structured_executor import execute_plan
from contracts import CitationValidationError, GenerationUnavailableError
from generation.answer_presenter import AnswerPresenter
from generation.policy_formatter import deterministic_policy_answer
from generation.prompts import REFUSAL_TEXT
from swufe_rag.orchestration import (
    HybridRuntime,
    SessionState,
    _source_appendix,
    _summary,
)
from swufe_rag.clarification import clarification_text
from swufe_rag.normalization_service import normalize_query
from swufe_rag.query_plan_schema import NormalizedQuery, UnderstandingDraft
from swufe_rag.query_understanding import (
    QuestionUnderstandingService,
    deterministic_understanding,
)
from swufe_rag.routing.schemas import RouteDecision
from swufe_rag.tool_planner import build_execution_plan


TOPICS = (
    (re.compile(r"推免|保研|推荐免试"), "promotion"),
    (re.compile(r"缓考|考试|考核"), "assessment"),
    (re.compile(r"选课操作|选课指南|怎么选课"), "course_selection"),
    (re.compile(r"学籍|重修|挂科|补考|旷考|休学|复学|转学|学业预警|学业警示|试读|退学|延毕|结业|肄业"), "academic_status"),
    (re.compile(r"转专业|专业分流"), "transfer"),
)


EVIDENCE_TOPIC_GATES = (
    (re.compile(r"奖学金"), re.compile(r"奖学金|奖助"), re.compile(r"奖学金"), "奖学金"),
    (re.compile(r"助学金"), re.compile(r"助学金|奖助"), re.compile(r"助学金"), "助学金"),
    (re.compile(r"勤工助学"), re.compile(r"勤工助学"), re.compile(r"勤工助学"), "勤工助学"),
    (
        re.compile(r"学生证.{0,12}(?:丢|遗失|补办)"),
        re.compile(r"学生证|学籍"),
        re.compile(r"学生证.{0,24}(?:丢失|遗失|补办)|(?:丢失|遗失|补办).{0,24}学生证"),
        "学生证补办",
    ),
    (re.compile(r"在读证明|学籍证明"), re.compile(r"在读证明|学籍证明|学籍"), re.compile(r"在读证明|学籍证明"), "在读证明"),
    (re.compile(r"(?:四六级|四级|六级).{0,16}报名|报名.{0,16}(?:四六级|四级|六级)"), re.compile(r"四六级|四、六级|大学英语"), re.compile(r"报名"), "四六级报名"),
    (re.compile(r"校历"), re.compile(r"校历"), re.compile(r"校历"), "校历"),
    (re.compile(r"寒假"), re.compile(r"寒假|放假"), re.compile(r"寒假"), "寒假安排"),
    (re.compile(r"校车|班车"), re.compile(r"校车|班车"), re.compile(r"校车|班车"), "校车"),
    (re.compile(r"心理咨询.{0,12}(?:预约|怎么|如何)|(?:预约|怎么|如何).{0,12}心理咨询"), re.compile(r"心理咨询|心理健康"), re.compile(r"咨询|预约"), "心理咨询"),
    (re.compile(r"请假|销假"), re.compile(r"请假|销假|考勤"), re.compile(r"请假|销假"), "请销假"),
    (re.compile(r"借书|借阅"), re.compile(r"图书馆|借阅"), re.compile(r"借书|借阅"), "图书借阅"),
    (re.compile(r"(?:教室|场地).{0,12}(?:预约|借用)"), re.compile(r"教室|场地"), re.compile(r"预约|借用"), "教室预约"),
    (
        re.compile(r"退宿|换寝|调宿|更换宿舍|调整寝室"),
        re.compile(r"宿舍|公寓|住宿"),
        re.compile(r"退宿|换寝|调宿|更换宿舍|寝室调整"),
        "宿舍调整",
    ),
    (
        re.compile(r"(?:校园卡|一卡通).{0,16}(?:丢|遗失|挂失|补办)"),
        re.compile(r"校园卡|一卡通"),
        re.compile(r"丢失|遗失|挂失|补办"),
        "校园卡补办",
    ),
    (
        re.compile(r"学生医保|医疗报销|医保.{0,12}报销"),
        re.compile(r"医保|医疗"),
        re.compile(r"医保|医疗报销|报销"),
        "学生医保",
    ),
    (
        re.compile(r"(?:体育馆|游泳馆|运动场).{0,16}(?:开放|营业|几点|预约)"),
        re.compile(r"体育馆|游泳馆|运动场|场馆"),
        re.compile(r"开放|营业|预约"),
        "体育场馆",
    ),
    (
        re.compile(r"(?:社团|学生组织).{0,16}(?:招新|报名|申请|加入)"),
        re.compile(r"社团|学生组织"),
        re.compile(r"招新|报名|申请|加入"),
        "学生社团",
    ),
    (
        re.compile(r"(?:毕业证|学位证|学历证书).{0,16}(?:丢|遗失|补办)"),
        re.compile(r"学历|学位|证书|学籍"),
        re.compile(
            r"(?:毕业证|学位证|学历证书).{0,80}(?:丢失|遗失|补办|证明书)|"
            r"(?:丢失|遗失|补办|证明书).{0,80}(?:毕业证|学位证|学历证书)"
        ),
        "毕业证书补办",
    ),
    (
        re.compile(r"成绩单.{0,16}(?:打印|办理|申请|开具)"),
        re.compile(r"成绩|学籍|证明"),
        re.compile(
            r"成绩单.{0,40}(?:打印|办理|申请|开具)|"
            r"(?:打印|办理|申请|开具).{0,40}成绩单"
        ),
        "成绩单办理",
    ),
    (
        re.compile(r"学费|住宿费"),
        re.compile(r"收费|学费|财务|缴费"),
        re.compile(r"学费.{0,30}(?:元|标准|收费)|(?:元|标准|收费).{0,30}学费"),
        "学费标准",
    ),
    (
        re.compile(r"(?:西财|西南财经大学|学校).{0,8}(?:校长|党委书记)"),
        re.compile(r"学校领导|现任领导|学校概况"),
        re.compile(r"校长|党委书记"),
        "学校现任领导",
    ),
    (
        re.compile(r"(?:西财|西南财经大学).{0,10}(?:985|211|双一流)|(?:985|211|双一流).{0,10}(?:西财|西南财经大学)"),
        re.compile(r"学校简介|学校概况|学校章程|建设高校|教育部"),
        re.compile(r"985工程|211工程|双一流|一流学科建设"),
        "学校办学层次",
    ),
)


def _missing_evidence_topics(
    question: str, chunks: list[dict[str, Any]]
) -> list[str]:
    missing: list[str] = []
    for question_re, title_re, text_re, label in EVIDENCE_TOPIC_GATES:
        if not question_re.search(question):
            continue
        if not any(
            title_re.search(str(chunk.get("doc_title") or ""))
            and text_re.search(str(chunk.get("text") or ""))
            for chunk in chunks
        ):
            missing.append(label)
    return missing


@dataclass(frozen=True)
class PipelineCapabilities:
    planner_llm: bool = False
    presenter_llm: bool = False
    policy_llm: bool = False
    general_llm: bool = False
    model: str | None = None


@dataclass
class QueryContextStore:
    """Semantic dialogue context shared by per-request model runtimes."""

    last_queries: dict[str, NormalizedQuery]
    pending_queries: dict[str, NormalizedQuery]
    context_questions: dict[str, str]

    @classmethod
    def empty(cls) -> "QueryContextStore":
        return cls(last_queries={}, pending_queries={}, context_questions={})


def _decision(query: NormalizedQuery) -> RouteDecision:
    school = query.domain == "school"
    return RouteDecision(
        mode="school_rag" if school else "general_chat",
        requires_school_facts=school,
        intent=query.primary_intent,
        college=query.college,
        cohort=str(query.cohort) if query.cohort else None,
        policy_year=None,
        rewritten_query=query.original_question,
        search_terms=tuple(
            value
            for value in (
                query.major,
                str(query.cohort) if query.cohort else None,
                *query.course_names,
                *query.course_codes,
                *query.subject_domains,
                *query.course_natures,
            )
            if value
        ),
        confidence=0.9,
    )


def _repair_draft_conflicts(draft: UnderstandingDraft, question: str) -> UnderstandingDraft:
    deterministic = deterministic_understanding(question)
    if deterministic.domain == "general":
        return deterministic.model_copy(update={"parser": draft.parser})
    if deterministic.primary_intent in {"policy", "promotion", "school_requirement"}:
        # Policy and campus-service questions must never become curriculum SQL
        # merely because the user has a stored program scope. The deterministic
        # recognizers are deliberately narrow and authoritative for this gate.
        return draft.model_copy(
            update={
                "domain": "school",
                "primary_intent": deterministic.primary_intent,
                "requested_outputs": deterministic.requested_outputs,
                "course_names": [],
                "course_codes": [],
                "subject_domain_mentions": [],
                "course_nature_mentions": [],
                "course_module_mentions": [],
                "information_scope": deterministic.information_scope,
            }
        )
    if (
        re.match(r"^\s*(?:改成|换成)", question)
        and deterministic.primary_intent == "school_requirement"
        and not deterministic.requested_outputs
        and (deterministic.major_mention or deterministic.cohort_mention)
    ):
        return deterministic.model_copy(update={"parser": draft.parser})
    campus_service = bool(
        draft.domain == "school"
        and re.search(
            r"食堂|自习室|校医院|洗衣房|校园卡|一卡通|快递|"
            r"超市|打印|文印|返校|报到|行课|停电|端午节|"
            r"全国计算机等级考试|NCRE",
            question,
            re.I,
        )
        and not re.search(r"培养方案|必修课|选修课|课程学分", question)
    )
    if campus_service:
        return draft.model_copy(
            update={
                "domain": "school",
                "primary_intent": "school_requirement",
                "requested_outputs": [],
                "course_names": [],
                "course_codes": [],
                "subject_domain_mentions": [],
                "course_nature_mentions": [],
                "course_module_mentions": [],
                "information_scope": "unknown",
            }
        )
    if draft.target_relation == "during_year_4" and re.search(
        r"大[一二三][上下].{0,10}(?:课|课程|选修|必修)", question
    ):
        return draft.model_copy(update={"target_relation": None})
    return draft


def _scope_only_reply(
    draft: UnderstandingDraft, normalized: NormalizedQuery
) -> bool:
    has_scope = bool(
        normalized.major
        or normalized.cohort
        or normalized.college
        or draft.major_mention
        or draft.cohort_mention
        or draft.college_mention
    )
    return bool(
        has_scope
        and draft.primary_intent == "school_requirement"
        and not draft.requested_outputs
        and not draft.course_names
        and not draft.course_codes
        and draft.current_stage is None
        and draft.target_stage is None
        and not draft.explicit_semesters
        and not draft.completed_course_mentions
        and not draft.completed_module_claims
        and not draft.completed_scope_claims
    )


def _merge_pending_scope(
    pending: NormalizedQuery, reply: NormalizedQuery
) -> NormalizedQuery:
    scope = {
        "college": reply.college or pending.college,
        "major": reply.major or pending.major,
        "cohort": reply.cohort or pending.cohort,
    }
    missing = [
        field
        for field in pending.missing_fields
        if not (
            (field == "college" and scope["college"])
            or (field == "major" and scope["major"])
            or (field == "cohort" and scope["cohort"])
        )
    ]
    warnings = list(
        dict.fromkeys(
            [
                *pending.normalization_warnings,
                *reply.normalization_warnings,
            ]
        )
    )
    return pending.model_copy(
        update={
            **scope,
            "missing_fields": missing,
            "normalization_warnings": warnings,
        }
    )


SCHOOL_FOLLOW_UP_RE = re.compile(
    r"^\s*(?:那|那么|那如果|那要|那还|改成|换成|这个|这种情况|上述|具体|还需要|还要|"
    r"其中|缓考|课程|专业选修|专业必修|必修课|毕业|推免|需要|申请|办理|提交|证明|材料|申请材料|证明材料|所需材料|流程|条件|"
    r"最晚|截止|开考|审核|多久|什么时候|何时|申请时间|办理时间|哪里|怎么办|为什么|能否|可以吗|详细一点|"
    r"(?:请|麻烦)?(?:帮我)?(?:总结|概括|归纳)|"
    r"(?:换(?:个|一种)?|用).{0,10}(?:说法|说一遍|解释)|"
    r"(?:能|可以).{0,8}(?:详细|具体|简单).{0,8}(?:说|讲|解释)|"
    r"(?:能|可以).{0,4}(?:说|讲|解释).{0,8}(?:详细|具体|简单)|"
    r"还有|这(?:条|项)规定|它|"
    r"(?:如果|假如).{0,40}(?:呢|怎么办|可以吗|能否|会怎样|如何|还来得及))"
)

CONTEXT_NEUTRAL_RE = re.compile(
    r"^\s*(?:谢谢(?:你|您)?|感谢(?:你|您)?|好的?|好吧|明白了?|知道了?|收到|了解了?|嗯+|嗯嗯|ok|okay)\s*[！!。.，,]*\s*$",
    re.I,
)


def _school_follow_up(question: str) -> bool:
    clean = question.strip()
    return bool(len(clean) <= 80 and SCHOOL_FOLLOW_UP_RE.search(clean))


def _merge_school_follow_up(
    prior: NormalizedQuery, reply: NormalizedQuery
) -> NormalizedQuery:
    whole_program_credits = bool(
        reply.primary_intent == "graduation_requirement"
        and "credit_total" in reply.requested_outputs
        and not reply.course_modules
        and not reply.course_names
        and not reply.course_codes
        and not reply.target_semesters
    )
    inherit_semantics = bool(
        not whole_program_credits
        and (
            reply.domain == "general"
            or reply.primary_intent in {"school_requirement", prior.primary_intent}
        )
    )
    updates: dict[str, Any] = {
        "domain": "school",
        "college": reply.college or prior.college,
        "major": reply.major or prior.major,
        "cohort": reply.cohort or prior.cohort,
        "information_scope": prior.information_scope,
    }
    if inherit_semantics:
        updates.update(
            primary_intent=(
                prior.primary_intent
                if reply.domain == "general" or reply.primary_intent == "school_requirement"
                else reply.primary_intent
            ),
            requested_outputs=reply.requested_outputs or prior.requested_outputs,
            current_semester=reply.current_semester or prior.current_semester,
            target_semesters=reply.target_semesters or prior.target_semesters,
            deadline_semester=reply.deadline_semester or prior.deadline_semester,
            avoid_semesters=reply.avoid_semesters or prior.avoid_semesters,
            course_names=reply.course_names or prior.course_names,
            course_codes=reply.course_codes or prior.course_codes,
            subject_domains=reply.subject_domains or prior.subject_domains,
            course_natures=reply.course_natures or prior.course_natures,
            course_modules=reply.course_modules or prior.course_modules,
            completed_courses=reply.completed_courses or prior.completed_courses,
            completed_module_claims=(
                reply.completed_module_claims or prior.completed_module_claims
            ),
            completed_scope_claims=(
                reply.completed_scope_claims or prior.completed_scope_claims
            ),
            goal_mentions=reply.goal_mentions or prior.goal_mentions,
        )
    effective = reply.model_copy(update=updates)
    missing = [
        field
        for field in effective.missing_fields
        if not (
            (field == "college" and effective.college)
            or (field == "major" and effective.major)
            or (field == "cohort" and effective.cohort)
            or (field == "semester" and effective.target_semesters)
        )
    ]
    if effective.primary_intent in {"school_requirement", "policy", "promotion"}:
        missing = [field for field in missing if field not in {"major", "cohort"}]
    return effective.model_copy(update={"missing_fields": missing})


def _clarification(missing: list[str]) -> str:
    labels = {
        "cohort": "入学年级",
        "major": "具体专业",
        "current_stage": "当前年级和上下学期",
        "completed_courses": "已修课程清单或成绩单",
    }
    readable = "、".join(labels.get(value, value) for value in missing)
    return f"还需要你补充：{readable}。信息齐全后我才能按对应培养方案准确查询。"


def _citation_page(citation: dict[str, Any]) -> int | None:
    article = str(citation.get("article") or "")
    page_url = str(citation.get("page_url") or "")
    match = re.search(r"原文件第(\d+)页", article)
    if match is None:
        match = re.search(r"(?:#|[?&])page=(\d+)", page_url)
    return int(match.group(1)) if match else None


def _compact_answer_citations(answer: dict[str, Any]) -> dict[str, Any]:
    citations = list(answer.get("citations") or [])
    mapping = {
        int(citation["marker"]): index
        for index, citation in enumerate(citations, start=1)
    }

    def replace(match: re.Match[str]) -> str:
        marker = int(match.group(1))
        return f"[{mapping.get(marker, marker)}]"

    return {
        **answer,
        "answer_md": re.sub(r"\[(\d+)\]", replace, str(answer["answer_md"])),
        "citations": [
            {**citation, "marker": mapping[int(citation["marker"])]}
            for citation in citations
        ],
    }


class QueryPipelineRuntime(HybridRuntime):
    def __init__(
        self,
        *,
        understanding: QuestionUnderstandingService,
        presenter: AnswerPresenter,
        academic_db: AcademicDatabase,
        capabilities: PipelineCapabilities | None = None,
        query_context: QueryContextStore | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.understanding = understanding
        self.presenter = presenter
        self.academic_db = academic_db
        self.capabilities = capabilities or PipelineCapabilities()
        self.query_context = query_context or QueryContextStore.empty()
        self._last_queries = self.query_context.last_queries
        self._pending_queries = self.query_context.pending_queries
        self._context_questions = self.query_context.context_questions
        # The HTTP adapter binds the process-local limiter here.  Keeping the
        # hook optional preserves the Python/runtime contract for offline tests
        # and callers that do not need admission control.
        self._retrieval_capacity: Any | None = None

    def bind_retrieval_capacity(self, capacity: Any | None) -> None:
        """Bind a capacity limiter around expensive retrieval only.

        SQL execution, routing, and remote answer generation must not consume
        the same MPS/CPU retrieval slots.  The adapter supplies an object with
        an ``acquire()`` context manager, avoiding an import from the HTTP
        layer into the query pipeline.
        """

        self._retrieval_capacity = capacity

    @staticmethod
    def _decode_stored_query(value: Any) -> NormalizedQuery | None:
        if isinstance(value, NormalizedQuery):
            return value
        if not isinstance(value, dict):
            return None
        try:
            return NormalizedQuery.model_validate(value)
        except (TypeError, ValueError):
            return None

    def _prior_query(
        self, state: SessionState, session_id: str | None
    ) -> NormalizedQuery | None:
        stored = self._decode_stored_query(state.last_normalized_query)
        if stored is not None:
            return stored
        return self._last_queries.get(session_id) if session_id else None

    def _pending_query(
        self, state: SessionState, session_id: str | None
    ) -> NormalizedQuery | None:
        stored = self._decode_stored_query(state.pending_normalized_query)
        if stored is not None:
            return stored
        return self._pending_queries.get(session_id) if session_id else None

    def _remember_query_context(
        self,
        state: SessionState,
        session_id: str | None,
        normalized: NormalizedQuery,
        *,
        pending: bool,
        context_question: str | None,
    ) -> None:
        payload = normalized.model_dump(mode="json")
        state.last_normalized_query = payload
        state.pending_normalized_query = payload if pending else None
        state.context_question = context_question
        if session_id:
            # Keep the legacy in-process maps warm for compatibility with
            # request runtimes created before this session-state migration.
            self._last_queries[session_id] = normalized
            if pending:
                self._pending_queries[session_id] = normalized
            else:
                self._pending_queries.pop(session_id, None)
            if context_question:
                self._context_questions[session_id] = context_question
            else:
                self._context_questions.pop(session_id, None)

    def record_cached_response(
        self,
        question: str,
        payload: dict[str, Any],
        *,
        session_id: str | None,
    ) -> bool:
        """Hydrate dialogue memory when an HTTP answer cache entry is used."""
        if session_id is None:
            return True
        normalized = self._decode_stored_query(payload.get("normalized_query"))
        if normalized is None or normalized.domain != "school":
            return False
        state = self.sessions.get(session_id)
        state.general_history.clear()
        self._remember_query_context(
            state,
            session_id,
            normalized,
            pending=False,
            context_question=question,
        )
        state.record_route(question, _decision(normalized))
        return True

    @classmethod
    def from_base(
        cls,
        base: HybridRuntime,
        *,
        academic_db: AcademicDatabase,
        understanding: QuestionUnderstandingService | None = None,
        presenter: AnswerPresenter | None = None,
        capabilities: PipelineCapabilities | None = None,
    ) -> "QueryPipelineRuntime":
        return cls(
            understanding=understanding or QuestionUnderstandingService(),
            presenter=presenter or AnswerPresenter(),
            academic_db=academic_db,
            capabilities=capabilities,
            router=base.router,
            school_retrieve=base.school_retrieve,
            school_answer=base.school_answer,
            general_chat=base.general_chat,
            metadata_db=base.metadata_db,
            sessions=base.sessions,
            runtime_mode=f"{base.mode}+typed-query-pipeline",
            runtime_info=getattr(base, "runtime_info", {}),
            query_context=(
                base.query_context
                if isinstance(base, QueryPipelineRuntime)
                else None
            ),
        )

    def can_stream_general(
        self,
        question: str,
        *,
        college: str | None = None,
        cohort: str | None = None,
        major: str | None = None,
        session_id: str | None = None,
    ) -> bool:
        draft = deterministic_understanding(
            question,
            college=college,
            cohort=cohort,
            major=major,
        )
        if draft.domain != "general":
            return False
        state = self.sessions.get(session_id)
        prior = self._prior_query(state, session_id)
        return not bool(
            prior is not None
            and prior.domain == "school"
            and _school_follow_up(question)
        )

    def stream_general_question(
        self,
        question: str,
        *,
        college: str | None = None,
        cohort: str | None = None,
        major: str | None = None,
        session_id: str | None = None,
        web_context: str | None = None,
        web_sources: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        state = self.sessions.get(session_id)
        prior = self._prior_query(state, session_id)
        draft = deterministic_understanding(
            question,
            college=college,
            cohort=cohort,
            major=major,
        )
        if draft.domain != "general" or bool(
            prior is not None
            and prior.domain == "school"
            and _school_follow_up(question)
        ):
            raise ValueError("question is not eligible for direct general streaming")

        total_started = time.perf_counter()
        inherited_major = prior.major if prior is not None else None
        inherited_cohort = prior.cohort if prior is not None else None
        effective_major = major or inherited_major
        draft = deterministic_understanding(
            question,
            college=college,
            cohort=cohort,
            major=effective_major,
        )
        started = time.perf_counter()
        normalized = normalize_query(
            draft,
            question,
            database=self.academic_db,
            inherited_major=effective_major,
            inherited_cohort=inherited_cohort,
        )
        normalization_ms = round((time.perf_counter() - started) * 1000, 2)
        plan = build_execution_plan(normalized)
        decision = _decision(normalized)
        answer_streaming = self.general_chat.supports_streaming
        yield {
            "type": "meta",
            "mode": "general_chat",
            "execution_path": "general_llm",
            "answer_streaming": answer_streaming,
        }
        yield {
            "type": "status",
            "stage": "generating",
            "message": "正在生成回复",
        }
        if answer_streaming:
            fragments: list[str] = []
            if web_context:
                stream = self.general_chat.stream_answer(
                    question, state.general_history, web_context=web_context
                )
            else:
                stream = self.general_chat.stream_answer(question, state.general_history)
            for fragment in stream:
                if not isinstance(fragment, str) or not fragment:
                    continue
                fragments.append(fragment)
                yield {"type": "delta", "text": fragment}
            answer = "".join(fragments).strip()
        elif web_context:
            answer = self.general_chat.answer(
                question, state.general_history, web_context=web_context
            )
        else:
            answer = self.general_chat.answer(question, state.general_history)
        if not answer:
            raise GenerationUnavailableError("general model returned an empty response")

        state.general_history.extend(
            [("user", question), ("assistant", answer)]
        )
        del state.general_history[:-24]
        if session_id:
            preserve_school_context = bool(
                prior is not None
                and prior.domain == "school"
                and CONTEXT_NEUTRAL_RE.fullmatch(question)
            )
            if not preserve_school_context:
                self._remember_query_context(
                    state,
                    session_id,
                    normalized,
                    pending=False,
                    context_question=None,
                )
        state.record_route(question, decision)

        total_ms = round((time.perf_counter() - total_started) * 1000, 2)
        payload = {
            "mode": "general_chat",
            "answer_md": answer,
            "citations": [],
            "retrieved": [],
            "official_links": [],
            "web_sources": list(web_sources or []),
            "refused": False,
            "latency_ms": total_ms,
            "execution_path": "general_llm",
            "planner_llm": {
                "called": False,
                "accepted": False,
                "latency_ms": 0.0,
            },
            "normalization": {
                "passed": True,
                "warnings": normalized.normalization_warnings,
                "latency_ms": normalization_ms,
            },
            "validation": {"passed": True, "checks": ["domain_boundary"]},
            "final_output_source": (
                "llm" if self.capabilities.general_llm else "deterministic_formatter"
            ),
            "llm_called": self.capabilities.general_llm,
            "llm_stages": {
                "question_understanding": False,
                "sql_execution": False,
                "rag_retrieval": False,
                "answer_generation": False,
                "general_generation": self.capabilities.general_llm,
                "fact_validation": True,
            },
            "normalized_query": normalized.model_dump(),
            "execution_plan": plan.model_dump(),
            "query_plan": normalized.model_dump(),
            "timings": {
                "question_understanding_ms": 0.0,
                "normalization_ms": normalization_ms,
                "sql_execution_ms": 0.0,
                "answer_generation_ms": total_ms,
                "total_ms": total_ms,
            },
        }
        yield {"type": "final", "response": payload}

    def attach_school_web_fallback(
        self,
        question: str,
        payload: dict[str, Any],
        *,
        web_sources: list[dict[str, Any]],
        search_ms: float = 0.0,
    ) -> dict[str, Any]:
        """Add an explicitly non-authoritative web answer after KB refusal.

        Public snippets never enter the school citation ledger. The original
        ``refused``/validation state remains true/false so clients can still
        distinguish a verified school answer from a best-effort web reference.
        """

        if (
            payload.get("mode") != "school_rag"
            or not payload.get("refused")
            or not self.capabilities.general_llm
            or not web_sources
        ):
            return payload

        started = time.perf_counter()
        body = self.general_chat.answer_school_web_fallback(question, web_sources)
        body = re.sub(r"\[([^\]]+)]\(https?://[^)]+\)", r"\1", body)
        body = re.sub(r"https?://\S+", "", body).strip()
        if not body:
            raise GenerationUnavailableError(
                "web fallback model returned an empty answer"
            )
        generation_ms = round((time.perf_counter() - started) * 1000, 2)
        disclaimer = (
            "当前校内知识库没有找到足以确认这个问题的学校官方依据。"
            "下面是根据公开网页搜索摘要整理的参考性推测，不代表西南财经大学现行规定；"
            "请最终以教务处、学院或学校最新官方通知为准。"
        )
        llm_stages = dict(payload.get("llm_stages") or {})
        llm_stages["answer_generation"] = True
        llm_stages["web_fallback_generation"] = True
        timings = dict(payload.get("timings") or {})
        timings["web_search_ms"] = round(float(search_ms), 2)
        timings["web_fallback_generation_ms"] = generation_ms
        validation = dict(payload.get("validation") or {})
        validation.update(
            passed=False,
            checks=list(validation.get("checks") or []) + ["web_not_official"],
        )
        return {
            **payload,
            "answer_md": f"{disclaimer}\n\n### 联网参考\n\n{body}",
            "web_sources": list(web_sources),
            "web_fallback": {
                "attempted": True,
                "used": True,
                "reason": "knowledge_base_insufficient",
                "source_count": len(web_sources),
                "search_ms": round(float(search_ms), 2),
                "generation_ms": generation_ms,
            },
            "final_output_source": "llm_web_fallback",
            "fallback_reason": "knowledge_base_insufficient",
            "llm_called": True,
            "llm_stages": llm_stages,
            "validation": validation,
            "timings": timings,
            "latency_ms": round(
                float(payload.get("latency_ms") or 0)
                + float(search_ms)
                + generation_ms,
                2,
            ),
        }

    def _metadata_chunks(
        self,
        *,
        cohort: int | None = None,
        title_like: str | None = None,
        article_like: str | None = None,
        text_like: str | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        clauses = ["s.enabled = 1"]
        params: list[Any] = []
        for column, value in (("s.cohort", str(cohort) if cohort else None),
                              ("s.doc_title", title_like),
                              ("c.article", article_like),
                              ("c.text", text_like)):
            if value is None:
                continue
            clauses.append(f"{column} {'LIKE' if '%' in value else '='} ?")
            params.append(value)
        params.append(limit)
        with self.metadata_db._lock:
            rows = self.metadata_db.connection.execute(
                f"""
                SELECT c.chunk_id, c.text, c.article, c.is_table,
                       s.doc_title, s.level, s.college, s.cohort, s.year, s.status,
                       s.page_url, s.file_url
                FROM chunks AS c JOIN sources AS s ON s.source_id = c.source_id
                WHERE {' AND '.join(clauses)}
                ORDER BY c.is_table DESC, c.embedding_row LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            {**dict(row), "is_table": bool(row["is_table"]), "score": 2.0}
            for row in rows
        ]

    def _authoritative_policy_chunks(self, query: NormalizedQuery) -> list[dict[str, Any]]:
        question = query.original_question
        requests: list[dict[str, Any]] = []
        policy_documents = (
            (r"转专业", "%本科生转专业管理办法%", "%第三章 转专业条件%"),
            (r"转学", "%本科生转学管理办法%", None),
            (r"挂(?:过)?科|补考|旷考|学业预警|学业警示|试读|退学|延毕|结业|肄业|学生证|在读证明|学籍证明|请假|销假", "%本科学生学籍管理规定%", None),
            (r"缓考", "%本科学生缓考规定%", None),
            (r"考试|考场|迟到|证件", "%学生考试规则%", None),
            (r"选课.{0,10}(?:步骤|流程|操作|指南)|(?:怎么|如何).{0,6}选课", "%学生选课操作指南%", None),
            (r"英语|外语|雅思|托福|GRE|GMAT", "%公共英语课程免修实施办法%", None),
            (r"免修(?:所有|全部)课程|(?:所有|全部)课程.{0,8}免修", "%公共英语课程免修实施办法%", None),
            (r"数字课程|数字学分", "%数字课程建设与学分认定%", None),
            (r"辅修", "%辅修学士学位管理办法（2024年版）%", None),
            (r"学士学位|学位授予", "%学位授予工作办法%", "%第三章 学位授予条件%"),
            (r"毕业论文|论文.{0,12}(?:查重|答辩|抽检|盲评)", "%本科毕业论文（设计）管理办法%", None),
            (r"优秀学术论文|论文.{0,8}奖励", "%优秀学术论文奖励实施办法%", None),
            (r"专业分流", "%专业分流管理办法%", None),
            (r"(?:期末|考试)?成绩(?:查询|有异议)|查卷|帮我查.{0,8}成绩", "%学生考试规则%", None),
            (r"教务系统.{0,10}(?:网址|地址|登录)", "%学生选课操作指南%", None),
        )
        for pattern, title_like, article_like in policy_documents:
            if not re.search(pattern, question, re.I):
                continue
            request: dict[str, Any] = {
                "cohort": None,
                "title_like": title_like,
                "limit": 32,
            }
            if article_like:
                request["article_like"] = article_like
            requests.append(request)
        # Year-scoped curriculum questions should cite the matching official
        # curriculum principle before a later course-recognition notice. The
        # latter is useful for current enrollment guidance, but it has no
        # physical page and can hide the authoritative annual rule.
        if re.search(r"艺术.{0,8}(?:学分|课程|认定)", question):
            curriculum_request: dict[str, Any] | None = None
            if query.cohort == 2023:
                curriculum_request = {
                    "cohort": 2023,
                    "title_like": "%西南财经大学2023级本科人才培养方案（完整总册）%",
                    "article_like": "%三、课程结构及学分要求%",
                    "limit": 16,
                }
            elif query.cohort == 2024:
                curriculum_request = {
                    "cohort": 2024,
                    "title_like": "%西南财经大学2024级本科人才培养方案（完整总册）%",
                    "article_like": "%通识课程板块%",
                    "limit": 32,
                }
            elif query.cohort == 2025:
                curriculum_request = {
                    "cohort": 2025,
                    "title_like": "%本科专业人才培养方案原则性意见（2025年版）%",
                    "article_like": "%三、课程结构及学分要求%",
                    "limit": 16,
                }
            if curriculum_request is not None:
                requests.append(curriculum_request)
            else:
                requests.append({
                    "cohort": None,
                    "title_like": "%艺术选修课程学分认定%",
                    "limit": 32,
                })
        if re.search(r"挂(?:过)?科|补考|旷考", question):
            requests.append({
                "cohort": None,
                "title_like": "%本科学生学籍管理规定%",
                "article_like": "%第四章 成绩考核与记载%",
                "limit": 24,
            })
        if re.search(r"学业预警|学业警示|试读|退学", question):
            requests.append({
                "cohort": None,
                "title_like": "%本科学生学籍管理规定%",
                "article_like": "%第七章 学业警示、试读与退学%",
                "limit": 24,
            })
        if re.search(r"\u901a\u8bc6\u6559\u80b2\u6838\u5fc3.*\u5b66\u5206", question):
            requests.append({"article_like": "%\u539f\u6587\u4ef6\u7b2c6\u9875%", "text_like": "%\u901a\u8bc6\u6559\u80b2\u6838\u5fc3%"})
        if re.search(r"\u4e13\u95e8\u7528\u9014\u82f1\u8bed|\u8de8\u6587\u5316\u4ea4\u9645|\u542c\u8bf4\u5199\u80fd\u529b\u8bad\u7ec3|\u5927\u5b66\u82f1\u8bed\u8bfe\u7a0b\u8bbe\u7f6e", question):
            requests.append({"article_like": "%\u539f\u6587\u4ef6\u7b2c9\u9875%", "text_like": "%ENG125%"})
        if "\u56fd\u9645\u4eba\u624d\u82f1\u8bed\u8003\u8bd5" in question:
            requests.append({"article_like": "%\u539f\u6587\u4ef6\u7b2c9\u9875%", "text_like": "%\u56fd\u9645\u4eba\u624d\u82f1\u8bed\u8003\u8bd5%"})
        if query.primary_intent == "promotion" or re.search(
            r"\u63a8\u514d|\u4fdd\u7814|\u63a8\u8350\u514d\u8bd5", question
        ):
            revision = "2023" if query.cohort and query.cohort <= 2023 else "2024"
            requests.append({
                "cohort": None,
                "title_like": f"%\u63a8\u8350\u514d\u8bd5\u7814\u7a76\u751f\u7ba1\u7406\u529e\u6cd5\uff08{revision}\u5e74\u4fee\u8ba2\uff09%",
                "limit": 40,
            })
        scoped_major = str(query.major or "").removesuffix("专业")
        for stem in ("\u8ba1\u7b97\u673a\u79d1\u5b66\u4e0e\u6280\u672f", "\u4eba\u5de5\u667a\u80fd"):
            if stem not in question and stem not in scoped_major:
                continue
            if "\u4e3b\u8981\u8bfe\u7a0b" in question:
                requests.append({"article_like": f"%{stem}\u4e13\u4e1a\u4eba\u624d\u57f9\u517b\u65b9\u6848 / \u4e94\u3001\u4e3b\u8981\u8bfe\u7a0b%"})
            if re.search(r"\u57f9\u517b\u76ee\u6807|\u5de5\u4f5c\u65b9\u5411|\u4ece\u4e8b.*\u5de5\u4f5c", question):
                requests.append({"article_like": f"%{stem}\u4e13\u4e1a\u4eba\u624d\u57f9\u517b\u65b9\u6848 / \u4e00\u3001\u57f9\u517b\u76ee\u6807%"})
        if (
            (query.primary_intent == "promotion" or re.search(r"推免|保研|推荐免试", question))
            and query.college == "计算机与人工智能学院"
        ):
            if query.cohort == 2023:
                requests.append({
                    "title_like": "%推荐免试研究生工作实施细则（2023级）%",
                    "limit": 32,
                })
        values: list[dict[str, Any]] = []
        for request in requests:
            values.extend(
                self._metadata_chunks(**{"cohort": query.cohort, **request})
            )
        return values

    def _cross_major_answer(self, query: NormalizedQuery) -> dict[str, Any] | None:
        question = query.original_question
        if not (re.search(r"\u8ba1\u7b97\u673a\u79d1\u5b66(?:\u4e0e\u6280\u672f)?", question)
                and "\u4eba\u5de5\u667a\u80fd" in question and query.cohort):
            return None
        majors = ("\u8ba1\u7b97\u673a\u79d1\u5b66\u4e0e\u6280\u672f\u4e13\u4e1a", "\u4eba\u5de5\u667a\u80fd\u4e13\u4e1a")
        citations: list[dict[str, Any]] = []
        by_chunk: dict[str, int] = {}

        def cite(chunk_id: str | None) -> int | None:
            if not chunk_id:
                return None
            if chunk_id in by_chunk:
                return by_chunk[chunk_id]
            stored = self.metadata_db.chunk(chunk_id)
            if stored is None:
                return None
            marker = len(citations) + 1
            page_match = re.search(r"(?:\u539f\u6587\u4ef6)?\u7b2c\s*(\d+)\s*\u9875", stored.article)
            citations.append({
                "marker": marker, "chunk_id": stored.chunk_id,
                "doc_title": stored.doc_title, "article": stored.article,
                "quote": stored.text[:800], "page_url": stored.page_url,
                "file_url": stored.file_url,
                "physical_page": int(page_match.group(1)) if page_match else None,
            })
            by_chunk[chunk_id] = marker
            return marker

        if re.search(r"\u5b9e\u8df5\u73af\u8282.*\u5b66\u5206", question):
            rows = []
            for major in majors:
                requirement = next((row for row in self.academic_db.requirements(cohort=query.cohort, major=major)
                                    if "\u5b9e\u8df5\u73af\u8282" in str(row.get("module") or "")), None)
                if requirement:
                    rows.append((major, requirement))
            if len(rows) == 2:
                parts = []
                for major, row in rows:
                    marker = cite(row.get("evidence_chunk_id"))
                    parts.append(f"{major.removesuffix('\u4e13\u4e1a')}{float(row['required_credits']):g}\u5b66\u5206" + (f"[{marker}]" if marker else ""))
                answer = "2023\u7ea7\u57f9\u517b\u65b9\u6848\u4e2d\uff0c" + "\uff0c".join(parts) + "\u3002"
                return {"mode": "school_rag", "answer_md": answer + _source_appendix(citations), "citations": citations,
                        "retrieved": [], "official_links": [], "refused": False}
        if "\u4e13\u4e1a\u5fc5\u4fee" in question:
            grouped: list[dict[str, dict[str, Any]]] = []
            for major in majors:
                rows = [row for row in self.academic_db.courses(cohort=query.cohort, major=major)
                        if "\u4e13\u4e1a\u5fc5\u4fee" in str(row.get("module") or "")]
                grouped.append({str(row.get("course_code")): row for row in rows if row.get("course_code")})
            common = sorted(set(grouped[0]) & set(grouped[1]))
            only_cs = sorted(set(grouped[0]) - set(grouped[1]))
            only_ai = sorted(set(grouped[1]) - set(grouped[0]))
            for rows in grouped:
                for row in rows.values():
                    cite(row.get("evidence_chunk_id"))
            def labels(codes: list[str], rows: dict[str, dict[str, Any]]) -> str:
                return "\u3001".join(f"{clean_course_name(rows[code].get('course_name'))}\uff08{code}\uff09" for code in codes)
            cs_marks = "".join(f"[{marker}]" for chunk, marker in by_chunk.items() if chunk in {row.get('evidence_chunk_id') for row in grouped[0].values()})
            ai_marks = "".join(f"[{marker}]" for chunk, marker in by_chunk.items() if chunk in {row.get('evidence_chunk_id') for row in grouped[1].values()})
            answer = (f"\u5171\u540c\u7684\u4e13\u4e1a\u5fc5\u4fee\u8bfe\u7a0b\u6709\uff1a{labels(common, grouped[0])}\u3002\n\n"
                      f"\u8ba1\u7b97\u673a\u79d1\u5b66\u4e0e\u6280\u672f\u4e13\u4e1a\u72ec\u6709\uff1a{labels(only_cs, grouped[0])}{cs_marks}\u3002\n\n"
                      f"\u4eba\u5de5\u667a\u80fd\u4e13\u4e1a\u72ec\u6709\uff1a{labels(only_ai, grouped[1])}{ai_marks}\u3002")
            return {"mode": "school_rag", "answer_md": answer + _source_appendix(citations), "citations": citations,
                    "retrieved": [], "official_links": [], "refused": False}
        return None

    def _policy(
        self,
        query: NormalizedQuery,
        top_k: int,
        *,
        contextual_question: str | None = None,
        claim_sink: Callable[[dict[str, Any]], None] | None = None,
        stream_cancelled: Callable[[], bool] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        contextual_question = contextual_question or query.original_question
        topic = next((value for pattern, value in TOPICS if pattern.search(contextual_question)), None)
        if query.major and re.search(
            r"本专业|主要课程|培养目标|工作方向|从事.*工作|专业准入|专业准出",
            contextual_question,
        ):
            contextual_question = f"{query.major} {contextual_question}"
        retrieval_started = time.perf_counter()
        capacity_wait_ms = 0.0

        evidence_query = (
            query
            if contextual_question == query.original_question
            else query.model_copy(update={"original_question": contextual_question})
        )
        enriched = self._authoritative_policy_chunks(evidence_query)

        def retrieve_chunks() -> list[dict[str, Any]]:
            return self.school_retrieve(
                contextual_question,
                top_k=max(top_k, 12),
                college=None,
                cohort=str(query.cohort) if query.cohort else None,
                policy_year=None,
                topic=topic,
            )

        if enriched:
            # Recognized policy topics already map to exact, enabled official
            # documents in trusted SQLite. Running vector encoding + cross-
            # encoder reranking first adds tens of seconds on CPU while the
            # same authoritative chunks are appended afterwards. Freeze this
            # exact evidence set immediately; use semantic retrieval only when
            # no deterministic policy selector produced evidence.
            chunks = list(
                {chunk["chunk_id"]: chunk for chunk in enriched}.values()
            )
            retrieval_source = "authoritative_metadata"
        elif self._retrieval_capacity is None:
            chunks = retrieve_chunks()
            retrieval_source = "semantic_retrieval"
        else:
            with self._retrieval_capacity.acquire() as lease:
                capacity_wait_ms = float(getattr(lease, "waited_ms", 0.0))
                chunks = retrieve_chunks()
            retrieval_source = "semantic_retrieval"
        retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 2)
        cross_major = self._cross_major_answer(query)
        if cross_major is not None:
            return cross_major, {"called": False, "retrieved_count": 0, "generation_accepted": True, "tool": "sql"}

        telemetry = {
            "called": True,
            "retrieved_count": len(chunks),
            "retrieval_ms": retrieval_ms,
            "capacity_wait_ms": round(capacity_wait_ms, 2),
            "retrieval_source": retrieval_source,
        }
        missing_topics = _missing_evidence_topics(contextual_question, chunks)
        if missing_topics:
            telemetry["generation_accepted"] = False
            telemetry["evidence_gate"] = {
                "passed": False,
                "missing_topics": missing_topics,
            }
            labels = "、".join(missing_topics)
            return {
                "mode": "school_rag",
                "answer_md": f"当前知识库中没有检索到与“{labels}”直接对应的学校官方文件，因此本轮不提供未经核验的答案。",
                "citations": [],
                "retrieved": [_summary(chunk) for chunk in chunks],
                "official_links": [],
                "refused": True,
            }, telemetry
        if not chunks:
            return {
                "mode": "school_rag",
                "answer_md": "当前知识库中没有检索到足够的可靠政策证据。",
                "citations": [],
                "retrieved": [],
                "official_links": [],
                "refused": True,

            }, telemetry
        telemetry["generation_attempted"] = self.capabilities.policy_llm
        telemetry["fallback_used"] = False
        generation_started = time.perf_counter()
        verified_claim_count = 0
        first_verified_claim_ms: float | None = None
        streamed_answer = None
        stream_owner = getattr(self.school_answer, "__self__", None)
        stream_answer = getattr(stream_owner, "stream_answer_polished", None)
        if (
            self.capabilities.policy_llm
            and claim_sink is not None
            and callable(stream_answer)
            and bool(getattr(stream_owner, "supports_verified_streaming", False))
        ):
            telemetry["verified_claim_stream"] = True
            for event in stream_answer(
                contextual_question,
                chunks,
                cancelled=stream_cancelled,
            ):
                if event.type == "claim" and event.claim is not None:
                    verified_claim_count += 1
                    if first_verified_claim_ms is None:
                        first_verified_claim_ms = round(
                            (time.perf_counter() - generation_started) * 1000,
                            2,
                        )
                    claim_sink(
                        {
                            "type": "claim",
                            "seq": event.claim.seq,
                            "text": event.claim.text,
                            "evidence_ids": list(event.claim.evidence_ids),
                        }
                    )
                elif event.type == "abort" and event.answer is not None:
                    claim_sink(
                        {
                            "type": "abort",
                            "reason": event.reason,
                            "answer_md": str(event.answer.get("answer_md") or ""),
                        }
                    )
                    telemetry["fallback_used"] = True
                    telemetry["fallback_reason"] = event.reason or "claim_validation_failed"
                elif event.type == "final" and event.answer is not None:
                    streamed_answer = event.answer
            if streamed_answer is None:
                raise GenerationUnavailableError(
                    "verified school stream ended without a final answer",
                    code="stream_ended_early",
                )
            raw = streamed_answer
        elif self.capabilities.policy_llm:
            try:
                raw = self.school_answer(contextual_question, chunks)
            except GenerationUnavailableError as exc:
                raw = deterministic_policy_answer(contextual_question, chunks)
                telemetry["fallback_used"] = True
                telemetry["fallback_reason"] = type(exc).__name__
            if raw.get("refused") or raw.get("answer_md") == REFUSAL_TEXT:
                raw = deterministic_policy_answer(contextual_question, chunks)
                telemetry["fallback_used"] = True
                telemetry["fallback_reason"] = "llm_refused"
        else:
            raw = deterministic_policy_answer(contextual_question, chunks)
        telemetry["generation_ms"] = round(
            (time.perf_counter() - generation_started) * 1000,
            2,
        )
        if telemetry.get("verified_claim_stream"):
            telemetry["verified_claim_count"] = verified_claim_count
            telemetry["first_verified_claim_ms"] = first_verified_claim_ms
        if raw.get("refused") or raw.get("answer_md") == REFUSAL_TEXT:
            telemetry["generation_accepted"] = False
            return {
                "mode": "school_rag",
                "answer_md": "检索到了相关材料，但当前证据不足以形成通过校验的答案。",
                "citations": [],
                "retrieved": [_summary(chunk) for chunk in chunks],
                "official_links": [],
                "refused": True,
            }, telemetry
        try:
            answer = self.binder.bind(raw, chunks)
        except CitationValidationError:
            if self.capabilities.policy_llm and not telemetry["fallback_used"]:
                raw = deterministic_policy_answer(contextual_question, chunks)
                telemetry["fallback_used"] = True
                telemetry["fallback_reason"] = "llm_citation_validation_failed"
                try:
                    answer = self.binder.bind(raw, chunks)
                except CitationValidationError:
                    answer = None
            else:
                answer = None
        if answer is None:
            telemetry["generation_accepted"] = False
            return {
                "mode": "school_rag",
                "answer_md": "模型回答没有通过引用校验，本轮未返回未经验证的内容。",
                "citations": [],
                "retrieved": [_summary(chunk) for chunk in chunks],
                "official_links": [],
                "refused": True,
            }, telemetry
        # Claim events use the retrieval-position marker seen by the validator.
        # Preserve those markers for a streamed turn so the final response does
        # not renumber citations that the user has already seen.
        if claim_sink is None:
            answer = _compact_answer_citations(answer)
        telemetry["generation_accepted"] = True
        citations = [
            {**citation, "physical_page": _citation_page(citation)}
            for citation in answer["citations"]
        ]
        answer_md = answer["answer_md"]
        if citations:
            answer_md += _source_appendix(citations)
        return {
            "mode": "school_rag",
            **answer,
            "answer_md": answer_md,
            "citations": citations,
            "retrieved": [_summary(chunk) for chunk in chunks],
            "official_links": [],
        }, telemetry

    def handle_question(
        self,
        question: str,
        *,
        college: str | None = None,
        cohort: str | None = None,
        major: str | None = None,
        session_id: str | None = None,
        top_k: int = 12,
        include_route_debug: bool = False,
        web_context: str | None = None,
        web_sources: list[dict[str, Any]] | None = None,
        claim_sink: Callable[[dict[str, Any]], None] | None = None,
        stream_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        total_started = time.perf_counter()
        state = self.sessions.get(session_id)
        prior = self._prior_query(state, session_id)
        inherited_major = prior.major if prior is not None else None
        inherited_cohort = prior.cohort if prior is not None else None
        effective_major = major or inherited_major

        started = time.perf_counter()
        draft = self.understanding.understand(
            question,
            college=college,
            cohort=cohort,
            major=effective_major,
        )
        draft = _repair_draft_conflicts(draft, question)
        planner_ms = round((time.perf_counter() - started) * 1000, 2)

        started = time.perf_counter()
        normalized = normalize_query(
            draft,
            question,
            database=self.academic_db,
            inherited_major=effective_major,
            inherited_cohort=inherited_cohort,
        )
        pending = self._pending_query(state, session_id)
        contextual_question: str | None = None
        if pending is not None and _scope_only_reply(draft, normalized):
            normalized = _merge_pending_scope(pending, normalized)
        elif prior is not None and prior.domain == "school" and _school_follow_up(question):
            # A short school-procedure or curriculum follow-up can be parsed
            # as a new intent, but it still belongs to the prior school turn.
            should_inherit = bool(
                normalized.domain == "general"
                or _school_follow_up(question)
                or normalized.primary_intent in {
                    "school_requirement",
                    prior.primary_intent,
                }
            )
            if should_inherit:
                normalized = _merge_school_follow_up(prior, normalized)
                previous_context = state.context_question or (
                    self._context_questions.get(session_id)
                    if session_id
                    else None
                ) or prior.original_question
                contextual_question = (
                    f"{previous_context}\n追问：{question.strip()}"[-2000:]
                )
        plan = build_execution_plan(normalized)
        normalization_ms = round((time.perf_counter() - started) * 1000, 2)
        decision = _decision(normalized)
        rag_telemetry: dict[str, Any] = {"called": False, "retrieved_count": 0}
        presenter_telemetry: dict[str, Any] = {"called": False, "accepted": False, "latency_ms": 0.0}
        execution_telemetry: dict[str, Any] = {
            "operations": [value.name for value in plan.operations],
            "coverage": {},
            "row_count": 0,
            "latency_ms": 0.0,
        }

        if plan.execution_path == "general_llm":
            payload = self._general(
                question, decision, state, web_context=web_context
            )
            final_source = "llm" if self.capabilities.general_llm else "deterministic_formatter"
        elif plan.execution_path == "clarify":
            message = clarification_text(
                plan.missing_fields,
                database=self.academic_db,
                cohort=normalized.cohort,
                major_mention=draft.major_mention or normalized.college,
            )
            payload = self._clarification(decision, message)
            final_source = "clarification"
        elif plan.execution_path == "rag":
            payload, rag_telemetry = self._policy(
                normalized,
                top_k,
                contextual_question=contextual_question,
                claim_sink=claim_sink,
                stream_cancelled=stream_cancelled,
            )
            if not rag_telemetry.get("generation_accepted"):
                final_source = "insufficient"
            elif self.capabilities.policy_llm and not rag_telemetry.get("fallback_used"):
                final_source = "llm"
            else:
                final_source = "deterministic_formatter"
        else:
            started = time.perf_counter()
            packet = execute_plan(plan, database=self.academic_db, metadata=self.metadata_db)
            execution_telemetry.update(
                coverage=packet.coverage.model_dump(),
                row_count=len(packet.courses),
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            started = time.perf_counter()
            presented = self.presenter.present(plan, packet)
            presenter_telemetry = {
                "called": presented.llm_called,
                "accepted": presented.llm_accepted,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "error": presented.error,
            }
            citations = [
                {
                    "marker": int(value.evidence_id[1:]),
                    "chunk_id": value.chunk_id,
                    "doc_title": value.doc_title,
                    "article": value.article,
                    "quote": value.quote,
                    "page_url": value.page_url,
                    "file_url": value.file_url,
                    "physical_page": value.physical_page,
                }
                for value in packet.citations
            ]
            answer_md = presented.answer_md
            if citations:
                answer_md += _source_appendix(citations)
            payload = {
                "mode": "school_rag",
                "answer_md": answer_md,
                "citations": citations,
                "retrieved": [],
                "official_links": [],
                "refused": False,
                "evidence_packet": packet.model_dump(),
            }
            final_source = presented.final_output_source

        if normalized.domain == "school":
            # General-chat history is a contiguous conversation segment. A school
            # turn ends that segment so later acknowledgements cannot revive it.
            state.general_history.clear()

        if session_id:
            preserve_school_context = bool(
                prior is not None
                and prior.domain == "school"
                and normalized.domain == "general"
                and CONTEXT_NEUTRAL_RE.fullmatch(question)
            )
            if not preserve_school_context:
                self._remember_query_context(
                    state,
                    session_id,
                    normalized,
                    pending=plan.execution_path == "clarify",
                    context_question=(
                        (contextual_question or question)
                        if normalized.domain == "school"
                        else None
                    ),
                )
        state.record_route(question, decision)
        total_ms = round((time.perf_counter() - total_started) * 1000, 2)
        payload.update(
            web_sources=list(web_sources or []),
            latency_ms=total_ms,
            execution_path=plan.execution_path,
            planner_llm={
                "called": draft.parser == "llm",
                "accepted": draft.parser == "llm",
                "latency_ms": planner_ms,
            },
            normalization={
                "passed": not bool(normalized.missing_fields),
                "warnings": normalized.normalization_warnings,
                "latency_ms": normalization_ms,
            },
            execution=execution_telemetry,
            rag=rag_telemetry,
            presenter_llm=presenter_telemetry,
            validation={
                "passed": final_source not in {"insufficient"},
                "checks": ["course_set", "credits", "semester", "citation", "raw_table_guard"],
            },
            final_output_source=final_source,
            fallback_reason=(
                presenter_telemetry.get("error")
                or rag_telemetry.get("fallback_reason")
            ),
            understanding_draft=draft.model_dump(),
            normalized_query=normalized.model_dump(),
            execution_plan=plan.model_dump(),
            # Legacy fields retained until the frontend migration is complete.
            llm_called=bool(
                draft.parser == "llm"
                or presenter_telemetry.get("called")
                or rag_telemetry.get("generation_attempted")
                or (
                    plan.execution_path == "general_llm"
                    and self.capabilities.general_llm
                )
            ),
            llm_stages={
                "question_understanding": draft.parser == "llm",
                "sql_execution": bool(plan.execution_path in {"sql", "sql+rag"}),
                "rag_retrieval": bool(rag_telemetry.get("called")),
                "answer_generation": bool(
                    (
                        presenter_telemetry.get("accepted")
                        and self.capabilities.presenter_llm
                    )
                    or (
                        rag_telemetry.get("generation_accepted")
                        and self.capabilities.policy_llm
                        and not rag_telemetry.get("fallback_used")
                    )
                ),
                "general_generation": bool(
                    plan.execution_path == "general_llm"
                    and self.capabilities.general_llm
                ),
                "fact_validation": final_source not in {"insufficient"},
            },
            query_plan=normalized.model_dump(),
            sql_coverage=execution_telemetry.get("coverage", {}).get("plan"),
            fallback=presenter_telemetry.get("error"),
            answer_generation_error=presenter_telemetry.get("error"),
            timings={
                "question_understanding_ms": planner_ms,
                "normalization_ms": normalization_ms,
                "sql_execution_ms": execution_telemetry.get("latency_ms", 0.0),
                "answer_generation_ms": max(
                    float(presenter_telemetry.get("latency_ms", 0.0) or 0.0),
                    float(rag_telemetry.get("generation_ms", 0.0) or 0.0),
                ),
                "total_ms": total_ms,
            },
        )
        if include_route_debug:
            payload["route"] = decision.to_dict()
        return payload

    def stream_school_question(
        self,
        question: str,
        *,
        college: str | None = None,
        cohort: str | None = None,
        major: str | None = None,
        session_id: str | None = None,
        top_k: int = 12,
        web_context: str | None = None,
        web_sources: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Run the synchronous pipeline while forwarding verified claims.

        A bounded queue gives the HTTP consumer backpressure. Disconnects set
        the cancellation flag, which is checked on every provider fragment so
        the upstream model stream is closed instead of continuing unseen.
        """

        queue: Queue[tuple[str, Any]] = Queue(maxsize=64)
        cancelled = Event()

        def put(kind: str, value: Any) -> None:
            while not cancelled.is_set():
                try:
                    queue.put((kind, value), timeout=0.1)
                    return
                except Full:
                    continue
            raise GenerationUnavailableError(
                "verified school stream was cancelled",
                code="stream_cancelled",
            )

        def run() -> None:
            try:
                response = self.handle_question(
                    question,
                    college=college,
                    cohort=cohort,
                    major=major,
                    session_id=session_id,
                    top_k=top_k,
                    web_context=web_context,
                    web_sources=web_sources,
                    claim_sink=lambda event: put("event", event),
                    stream_cancelled=cancelled.is_set,
                )
                put("final", response)
            except Exception as exc:
                if not cancelled.is_set():
                    try:
                        put("error", exc)
                    except GenerationUnavailableError:
                        pass

        worker = Thread(target=run, name="verified-school-stream", daemon=True)
        worker.start()
        try:
            while True:
                kind, value = queue.get()
                if kind == "event":
                    yield value
                elif kind == "final":
                    yield {"type": "final", "response": value}
                    return
                else:
                    raise value
        finally:
            cancelled.set()
            worker.join()

    def options(self) -> dict[str, Any]:
        value = super().options()
        value.update(self.academic_db.options())
        value["orchestration"] = "typed-query-pipeline"
        value["llm_capabilities"] = {
            "question_understanding": self.capabilities.planner_llm,
            "structured_generation": self.capabilities.presenter_llm,
            "rag_generation": self.capabilities.policy_llm,
            "general_generation": self.capabilities.general_llm,
            "model": self.capabilities.model,
        }
        return value


__all__ = ["PipelineCapabilities", "QueryContextStore", "QueryPipelineRuntime"]
