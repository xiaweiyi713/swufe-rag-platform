"""High-precision mixed-dialogue router with a fail-open general fallback."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Protocol

from generation.llm import LLMClient
from retrieval.query import normalize_query
from swufe_rag.routing.prompts import ROUTER_SYSTEM_PROMPT, build_router_prompt
from swufe_rag.routing.schemas import RouteContext, RouteDecision


class RouteClassifier(Protocol):
    def classify(self, question: str, context: RouteContext) -> Mapping[str, Any]: ...


class LLMRouteClassifier:
    """Converts an LLM response to strict routing JSON; it never executes it."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def classify(self, question: str, context: RouteContext) -> Mapping[str, Any]:
        response = self.client.generate(
            ROUTER_SYSTEM_PROMPT,
            build_router_prompt(
                question,
                last_mode=context.last_mode,
                last_intent=context.last_intent,
                last_college=context.last_college,
                last_cohort=context.last_cohort,
                last_rewritten_query=context.last_rewritten_query,
            ),
        ).strip()
        response = re.sub(r"^```(?:json)?\s*|\s*```$", "", response, flags=re.I)
        raw = json.loads(response)
        if not isinstance(raw, dict):
            raise ValueError("route classifier must return a JSON object")
        return raw


COHORT_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*(?:级|届)")
POLICY_YEAR_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*年"
    r"(?:(?:的)?(?:政策|规定|办法|通知|推免|保研|细则|方案)|版)"
)
FOLLOW_UP_RE = re.compile(
    r"^(?:那|那么|然后|还有|还|这个|这种情况|重修通过后|以后|之后)"
    r"[^。！？!?]{0,30}[？?]?$"
)
SCHOOL_ENTITY_RE = re.compile(
    r"西南财经大学|西南财大|西财|SWUFE|教务处|学院教务办|计算机与人工智能学院|金融学院"
)
SCHOOL_POLICY_RE = re.compile(
    r"培养方案|推免|保研|推荐免试|选课|学分认定|毕业学分|专业选修|必修课|"
    r"修多少学分|多少学分|学分够不够|学分要求|"
    r"自由选修|自选课|专业方向|实践环节|课程设置|课程代码|课程课号|开课学期|课程学时|"
    r"计划学制|修业年限|专业准入|专业准出|授予学位|培养目标|"
    r"挂科|不及格|重修|考试|考核|缓考|休学|转学|转专业|专业分流|辅修|免修|学籍|"
    r"教务系统|校内通知|官方通知|教务处通知"
    r"|毕业论文|毕业设计|学位授予|奖学金|数字课程"
)
MAJOR_ENTITY_RE = re.compile(
    r"(?:[\u4e00-\u9fff]{2,30}(?:专业|专业类)|计算机科学|计科|"
    r"(?<![A-Za-z0-9])CS(?:专业)?(?![A-Za-z0-9])|"
    r"人工智能|(?<![A-Za-z0-9])AI专业(?![A-Za-z0-9])|智能专业)",
    re.I,
)
ACADEMIC_STAGE_RE = re.compile(
    r"大[一二三四](?:上|下)?|第[一二三四五六七八九十0-9]+学期|暑期学期|春季学期|秋季学期"
)
CURRICULUM_FACT_RE = re.compile(
    r"课程|什么课|啥课|修.*课|学什么|学分|修几分|学制|修业年限|学位|准入|准出|"
    r"学期|学时|代码|课号|选修|必修|方向课|实践环节|实践课|培养|毕业|教学周|课程模块"
)
COURSE_DETAIL_RE = re.compile(
    r"课程.{0,20}(?:代码|学分|学时|学期|开设|设置|模块|必修|选修|学院|几门)|"
    r"(?:几门|代码|课号|学分|学时|学期).{0,20}课程|"
    r"课程.{0,20}(?:选择|有哪些)|在哪些?个?学期开设|哪个学院开设"
 )
CURRICULUM_ENTITY_RE = re.compile(
    r"公共外语|大学英语|专门用途英语|跨文化交际|听说写能力训练|艺术类课程|"
    r"体育课程|军事教育|思想政治|数学课程|程序设计|专业必修|专业方向"
)
CAMPUS_FACT_RE = re.compile(
    r"校园网|食堂|宿舍|学生公寓|校车|图书馆|自习室|空教室|洗衣房|校医院|"
    r"柳林校区|光华校区|校园卡|一卡通|快递|文印|超市|返校|开学|报到|"
    r"行课|放假|军训|停电|端午节|学生.{0,4}暑假|"
    r"暑假.{0,8}(?:放假|开学|返校|报到|行课|几号)|全国计算机等级考试|NCRE|腾骧楼|弘远楼|"
    r"格致楼|通博楼|诚正楼|学生活动中心|光华楼|博学园|信园|颐德楼|"
    r"校内邮箱|校内网站|校内网址"
)
FACT_REQUEST_RE = re.compile(
    r"多少|几分|几门|条件|要求|规定|办法|政策|时间|什么时候|几点|"
    r"怎么办|还能|可以|是否|哪里|在哪|网址|链接|原文|通知|怎么选|应该选|"
    r"忘了|丢了|补办"
)
GENERAL_TASK_RE = re.compile(
    r"写代码|改代码|调试|翻译|润色|改写|写一封|写封|写邮件|写作文|"
    r"写策划|讲个笑话|编个故事|陪我聊|压力很大|心情不好|解释注意力机制|"
    r"什么是注意力机制|排序算法|Python|Java|SQL语法"
)
GENERAL_SWITCH_RE = re.compile(
    r"换个话题|不说这个了|别说这个了|给我写代码|讲个笑话|帮我翻译|帮我润色"
)
PROGRAMMING_PROBLEM_RE = re.compile(
    r"leetcode|力扣|hot\s*100|codeforces|洛谷|算法题|编程题|"
    r"(?:第\s*\d+\s*题).{0,32}(?:题解|解答|代码|实现)|"
    r"(?:题解|解答|代码|实现).{0,32}(?:第\s*\d+\s*题)",
    re.I,
)
SCHOOL_DECISION_RE = re.compile(
    r"推免|保研|学分|选修|必修|课程|哪门课|什么课|培养方案|学籍|"
    r"学校规定|学校要求|教务|西财|西南财经大学"
)

INTENT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"推免|保研|推荐免试|竞赛.*加分|比赛.*加分"), "promotion"),
    (
        re.compile(
            r"培养方案|毕业.*学分|总学分|最低学分|专业选修|自由选修|自选课|必修课|"
            r"选修课|有什么.*课|有哪些.*课|体育课程|修读|修满|修什么课|"
            r"要修什么课|哪些课程|选修课程|课程设置|学制|修业年限|专业准入|"
            r"专业准出|实践环节|开课学期|第[一二三四五六七八1-8]学期.*课|大[一二三四].*课"
        ),
        "curriculum",
    ),
    (re.compile(r"选课|应该选|怎么选"), "course_selection"),
    (re.compile(r"转专业|专业分流"), "transfer"),
    (re.compile(r"学分认定|免修|辅修"), "credit"),
    (re.compile(r"考试|缓考|考核"), "assessment"),
    (re.compile(r"挂科|不及格|重修|休学|学籍|转学"), "academic_status"),
    (CAMPUS_FACT_RE, "campus_service"),
)


def _intent(question: str) -> str:
    if re.search(r"[A-Za-z]{2,}\d{2,4}", question) and re.search(
        r"课程|什么课|学分|选修|必修", question
    ):
        return "curriculum"
    for pattern, value in INTENT_RULES:
        if pattern.search(question):
            return value
    return "school_general"


def _search_terms(question: str, intent: str) -> tuple[str, ...]:
    additions = {
        "promotion": ("推荐免试", "推免资格", "综合成绩"),
        "curriculum": ("培养方案", "课程设置", "毕业要求"),
        "course_selection": ("选课", "课程设置"),
        "transfer": ("转专业", "申请条件"),
        "assessment": ("课程考核", "考试规定"),
        "academic_status": ("学籍管理", "重修", "不及格"),
        "credit": ("学分认定",),
        "campus_service": ("校内服务",),
    }.get(intent, ())
    entities = re.findall(r"[A-Za-z]{2,}\d{2,4}|[\u4e00-\u9fff]{2,12}", question)
    return tuple(dict.fromkeys([*entities[:6], *additions]))[:10]


class HybridRouter:
    def __init__(
        self,
        classifier: RouteClassifier | None = None,
        *,
        known_colleges: tuple[str, ...] = (),
    ) -> None:
        self.classifier = classifier
        self.known_colleges = known_colleges

    def _college(self, question: str, explicit: str | None) -> str | None:
        if explicit and explicit.strip():
            return explicit.strip()
        for college in self.known_colleges:
            if college in question:
                return college
        for common in ("计算机与人工智能学院", "金融学院"):
            if common in question:
                return common
        if re.search(r"计算机科学与技术|人工智能专业", question):
            return "计算机与人工智能学院"
        return None

    @staticmethod
    def _cohort(question: str, explicit: str | None) -> str | None:
        if explicit and explicit.strip():
            return explicit.strip()
        match = COHORT_RE.search(question)
        return match.group(1) if match else None

    @staticmethod
    def _policy_year(question: str) -> int | None:
        match = POLICY_YEAR_RE.search(question)
        return int(match.group(1)) if match else None

    @staticmethod
    def _explicit_general(question: str) -> bool:
        if GENERAL_SWITCH_RE.search(question):
            return True
        if PROGRAMMING_PROBLEM_RE.search(question):
            return not SCHOOL_DECISION_RE.search(question)
        if GENERAL_TASK_RE.search(question):
            if SCHOOL_POLICY_RE.search(question):
                return False
            school_fact = SCHOOL_ENTITY_RE.search(question)
            writing_request = re.search(r"写|翻译|润色|改写|代码|笑话|故事", question)
            return bool(writing_request or not school_fact)
        return False

    @staticmethod
    def _definite_school_fact(question: str) -> bool:
        if SCHOOL_POLICY_RE.search(question):
            return True
        if COURSE_DETAIL_RE.search(question):
            return True
        if CURRICULUM_FACT_RE.search(question) and (
            COHORT_RE.search(question)
            or MAJOR_ENTITY_RE.search(question)
            or ACADEMIC_STAGE_RE.search(question)
            or CURRICULUM_ENTITY_RE.search(question)
        ):
            return True
        if re.search(r"[A-Za-z]{2,}\d{2,4}", question) and re.search(
            r"学分|课程|什么课|选修|必修", question
        ):
            return True
        if CAMPUS_FACT_RE.search(question) and FACT_REQUEST_RE.search(question):
            return True
        return bool(SCHOOL_ENTITY_RE.search(question) and FACT_REQUEST_RE.search(question))

    def _general(self, question: str, confidence: float = 0.9) -> RouteDecision:
        return RouteDecision(
            mode="general_chat",
            requires_school_facts=False,
            intent="general_chat",
            college=None,
            cohort=None,
            policy_year=None,
            rewritten_query=question,
            search_terms=(),
            confidence=confidence,
        )

    def _school(
        self,
        question: str,
        context: RouteContext,
        *,
        college: str | None,
        cohort: str | None,
        confidence: float,
    ) -> RouteDecision:
        intent = _intent(question)
        rewritten = question
        inherited = context.last_mode == "school_rag" and FOLLOW_UP_RE.search(question)
        scope_follow_up = (
            context.last_mode == "school_rag"
            and context.last_cohort is not None
            and COHORT_RE.search(question) is None
            and CURRICULUM_FACT_RE.search(question) is not None
            and intent in {"curriculum", "course_selection", "school_general"}
        )
        if scope_follow_up:
            cohort = cohort or context.last_cohort
        if inherited and context.last_rewritten_query:
            rewritten = f"{context.last_rewritten_query}；用户追问：{question}"
            if context.last_intent:
                intent = context.last_intent
            college = college or context.last_college
            cohort = cohort or context.last_cohort
        return RouteDecision(
            mode="school_rag",
            requires_school_facts=True,
            intent=intent,
            college=college,
            cohort=cohort,
            policy_year=self._policy_year(question),
            rewritten_query=rewritten,
            search_terms=_search_terms(rewritten, intent),
            confidence=confidence,
        )

    def route(
        self,
        question: str,
        *,
        context: RouteContext | None = None,
        college: str | None = None,
        cohort: str | None = None,
    ) -> RouteDecision:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must not be blank")
        clean = normalize_query(question)
        context = context or RouteContext()
        resolved_college = self._college(clean, college)
        resolved_cohort = self._cohort(clean, cohort)

        if self._explicit_general(clean):
            return self._general(clean, 0.99)
        if self._definite_school_fact(clean) or (
            (resolved_college or resolved_cohort) and CURRICULUM_FACT_RE.search(clean)
        ):
            return self._school(
                clean,
                context,
                college=resolved_college,
                cohort=resolved_cohort,
                confidence=0.99,
            )
        if (
            context.last_mode == "school_rag"
            and FOLLOW_UP_RE.search(clean)
            and not GENERAL_SWITCH_RE.search(clean)
        ):
            return self._school(
                clean,
                context,
                college=resolved_college,
                cohort=resolved_cohort,
                confidence=0.94,
            )

        if self.classifier is not None:
            try:
                decision = RouteDecision.from_mapping(
                    self.classifier.classify(clean, context),
                    question=clean,
                    known_colleges=self.known_colleges,
                )
            except Exception:
                decision = None
            if decision is not None:
                if decision.mode == "school_rag":
                    return RouteDecision(
                        mode="school_rag",
                        requires_school_facts=True,
                        intent=decision.intent,
                        college=resolved_college or decision.college,
                        cohort=resolved_cohort or decision.cohort,
                        policy_year=decision.policy_year,
                        rewritten_query=decision.rewritten_query,
                        search_terms=decision.search_terms,
                        confidence=decision.confidence,
                    )
                return decision

        # Classifier failure and genuinely ambiguous input are normal chat.
        # The high-precision checks above already prevent explicit school facts
        # from escaping to a general model.
        return self._general(clean, 0.7)


_default_router = HybridRouter()


def route_question(
    question: str,
    *,
    context: RouteContext | None = None,
    college: str | None = None,
    cohort: str | None = None,
) -> RouteDecision:
    return _default_router.route(
        question, context=context, college=college, cohort=cohort
    )


__all__ = [
    "HybridRouter",
    "LLMRouteClassifier",
    "RouteClassifier",
    "route_question",
]
