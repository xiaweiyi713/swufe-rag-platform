"""LLM-assisted but schema-bound question understanding for V16."""

from __future__ import annotations

import json
import re
from typing import Any

from generation.llm import LLMClient
from swufe_rag.query_plan_schema import AcademicStage, UnderstandingDraft


SCHOOL_RE = re.compile(
    r"课程|选课|学分|学时|学期|专业|学院|培养方案|毕业|选修|必修|推免|保研|缓考|"
    r"重修|挂科|补考|旷考|考试|考核|辅修|免修|学位|论文|分流|转学|学籍|成绩|查卷|教务|"
    r"学业预警|学业警示|试读|退学|延毕|结业|肄业|"
    r"奖学金|助学金|奖助学金|勤工助学|助学贷款|困难认定|贫困认定|"
    r"学生证|在读证明|学籍证明|成绩单|请假|销假|四六级|四级报名|六级报名|"
    r"图书馆|食堂|校园|校区|宿舍|退宿|换寝|调宿|学生公寓|空教室|自习室|洗衣房|校医院|"
    r"校园卡|一卡通|快递|文印|超市|校车|班车|返校|开学|报到|行课|校历|寒假|放假|军训|停电|端午节|"
    r"心理咨询|心理健康中心|辅导员|学生处|保卫处|财务处|后勤|借书|借阅|"
    r"社团|学生组织|招新|入团|入党|党团关系|"
    r"(?:实验室|教室|场地).{0,8}(?:预约|借用|开放)|体育馆|游泳馆|"
    r"学费|住宿费|学生医保|医疗报销|毕业档案|三方协议|就业推荐表|毕业去向|组织关系|"
    r"交换生|交流项目|第二课堂|竞赛加分|大创项目|科研训练|社会实践|"
    r"学生.{0,4}暑假|暑假.{0,8}(?:放假|开学|返校|报到|行课|几号)|"
    r"全国计算机等级考试|NCRE|腾骧楼|弘远楼|格致楼|通博楼|诚正楼|"
    r"学生活动中心|光华楼|博学园|信园|颐德楼|校内|学校规定|西财|西南财经大学|官网|"
    r"英语|体育|大[一二三四]|\d{2,4}级"
)
PROGRESS_RE = re.compile(
    r"已修|修完|还差|剩余|怎么安排|如何安排|应该怎么|提前修|大四不想|不排课|是否可行"
)
POLICY_RE = re.compile(
    r"推免|保研|缓考|重修|挂科|补考|旷考|转专业|转学|学籍|休学|复学|"
    r"学业预警|学业警示|试读|退学|延毕|结业|肄业|免修|辅修|学位|"
    r"奖学金|助学金|勤工助学|请假|销假|专业分流|学分认定|成绩查询|查卷|规定|办法|细则"
)
POLICY_QUESTION_RE = re.compile(
    r"考试|考核|选课.{0,10}(?:步骤|流程|操作|指南|规则|办法)|"
    r"(?:怎么|如何).{0,6}选课|论文.{0,12}(?:查重|答辩|抽检|盲评|规范|管理)|"
    r"(?:查重|答辩|抽检|盲评).{0,12}论文|优秀学术论文|论文.{0,8}奖励|"
    r"(?:艺术|数字课程|数字学分).{0,12}认定"
)
PROMOTION_RE = re.compile(r"推免|保研|推荐免试")
COURSE_CODE_RE = re.compile(
    r"(?<![A-Z0-9])([A-Z]{2,5}\d{3})(?![A-Z0-9])",
    re.I,
)
COHORT_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*(?:级|届)")
SHORT_COHORT_RE = re.compile(r"(?<!\d)(\d{2})\s*级")
EXPLICIT_GENERAL_CHAT_RE = re.compile(
    r"^\s*(?:你(?:好|好呀|好啊)|您好|嗨|哈[喽啰]|hello|hi|"
    r"早上好|中午好|下午好|晚上好|早安|晚安|"
    r"谢谢(?:你|您)?|感谢(?:你|您)?|你是谁|你能做什么|你会做什么)"
    r"\s*[！!。.？?，,]*\s*$",
    re.I,
)
GENERAL_TASK_RE = re.compile(
    r"^\s*(?:(?:请|帮我|给我|能否|可以)?\s*(?:翻译|润色|改写)|"
    r"(?:把|将).{1,80}(?:翻译成|译成)(?:英文|英语|中文|汉语|日文|日语|法文|法语)?|"
    r"(?:请|帮我|给我|能否|可以)?\s*(?:写|生成|设计|实现).{0,48}(?:作文|短文|文章|邮件|文案|代码|函数|程序|故事|诗|总结|申请书|申请材料|演讲稿|发言稿|简历|通知)|"
    r"(?:请|帮我|给我)?\s*(?:用|写).{0,16}(?:Python|Java|Swift|C\+\+|JavaScript).{0,40}|"
    r"讲(?:一个|个)?.{0,16}(?:笑话|故事)|"
    r".{0,20}(?:焦虑|难过|压力|心情).{0,24}(?:怎么办|安慰|缓解)|"
    r".{0,16}(?:怎么|如何).{0,16}(?:复习|学习|预习)|"
    r".{0,16}(?:前景|就业|职业发展).{0,16}(?:怎么样|如何)|"
    r"推荐.{0,24}(?:教材|书籍|书|课程)|"
    r".{0,12}食堂.{0,16}(?:怎么做|做法)|"
    r".{0,12}图书馆.{0,12}为什么.{0,12}(?:学习|安静)|"
    r".{0,16}(?:宿舍|校园|学校|图书馆).{0,20}(?:失眠|睡不着|孤独|交朋友|和同学相处).{0,20}|"
    r".{0,12}(?:辅导员|老师|同学|室友).{0,20}(?:批评|吵架|矛盾|不理).{0,16}(?:难受|难过|焦虑|怎么办))",
    re.I,
)
GENERAL_SCHOOL_CONCEPT_RE = re.compile(
    r"^\s*(?:什么是|解释一下|介绍一下)\s*(?:推免|保研|学分制|缓考|重修|辅修|专业分流)"
    r"\s*[？?。.！!]*\s*$"
)
INSTITUTION_FACT_RE = re.compile(
    r"西财|西南财经大学|我校|本校|学校规定|学校要求|教务系统|培养方案|柳林|光华"
)


def _explicit_general_task(question: str) -> bool:
    return bool(
        (GENERAL_TASK_RE.search(question) or GENERAL_SCHOOL_CONCEPT_RE.fullmatch(question))
        and not INSTITUTION_FACT_RE.search(question)
    )


UNDERSTANDING_PROMPT = """你是西南财经大学教务问题理解器。
只输出符合给定 JSON Schema 的语义草稿，不回答问题，不生成 SQL，不选择数据库操作。
必须保留专业、年级、当前阶段、相对时间、课程主题、课程性质、课程模块、已修课程或模块声明和用户目标。
把两位入学年级规范为四位年份，例如“23级”表示 cohort_mention=2023；同样处理其他两位年份。
专业简称、口语、错别字、学年上下学期和“下学期/大四前”等相对表达都由你理解并填入字段。
“大一/大二/大三/大四”必须写入 current_stage.year；未说上或下时 term=null，说“大三上/大三下”时分别写入 term="上"/"下"。
用户说“某时间范围内所有符合条件的课都选了/修完了”时，不要要求逐门课程名；填 completed_scope_claims。
“在当前学期之前”使用 semester_relation="before_current_semester"；“选了”使用 status="selected"，“修完”使用 completed，“通过”使用 passed，并保留课程性质或模块范围。
输出前逐项复核原问题，任何用户已经说出的约束都不得遗漏。
不要换算相对学期；不要猜测未提供的已修课程；不要输出 sql/table/url/operation/where_clause。
scope 仅提供学校问题的查询范围，不得因为 scope 中有专业、学院或年级而把寒暄或通用问题改判为 school。
“培养方案安排”与“实际开课可选”必须区分。"""


UNDERSTANDING_REVIEW_PROMPT = """你是教务语义 JSON 审核器。
对照 original_question 审核 candidate_draft，修复遗漏、误解或冲突后，只输出符合 json_schema 的完整 JSON。
重点复核：入学年级、学院与具体专业、当前年级及上下学期、明确或相对目标学期、课程主题、必修/选修、课程模块、已修声明、规划目标、培养方案或实际开课范围。
特别检查所有“大一/大二/大三/大四”表达：即使没有“上/下”，也不得遗漏 current_stage；term 未给出时为 null。
若原问题包含“之前所有选修课都选了”等范围完成声明，必须恢复 completed_scope_claims，不得误判为完全没有已修信息。
所有语义判断由你完成；不得回答问题，不得生成 SQL、数据库操作、网址或自由文本。
scope 仅提供学校问题的查询范围，不得改变 original_question 本身的领域和意图。
两位入学年级必须规范成四位年份。不要补造原问题没有提供的事实。"""


# Semantic intent is still decided by the LLM.  These instructions make the
# contract explicit for planning questions that also contain prior-study
# information, instead of relying on brittle keyword remapping afterward.
UNDERSTANDING_PROMPT += """
If the user provides completed-course, completed-module, or completed-scope
information and asks for remaining credits/courses or how to arrange future
study, use primary_intent=\"progress_audit\". Include remaining_credits,
remaining_courses, and feasibility in requested_outputs when relevant.
All array fields must be [] rather than null when empty.
"""
UNDERSTANDING_REVIEW_PROMPT += UNDERSTANDING_PROMPT.split("If the user", 1)[1]


_SCOPE_RELATION_GUIDANCE = """
If a completed range is anchored to a named term the user is planning (for
example, all electives before junior spring), use
semester_relation=\"before_target_semester\". Use before_current_semester only
when the range is explicitly anchored to the user's current term.
"""
UNDERSTANDING_PROMPT += _SCOPE_RELATION_GUIDANCE
UNDERSTANDING_REVIEW_PROMPT += _SCOPE_RELATION_GUIDANCE

_EXPLICIT_TARGET_GUIDANCE = """
When the user names an academic term as the object to plan or query, always
put its absolute curriculum semester in explicit_semesters, even if the
user's current term is unknown. Convert year/term with semester=(year-1)*2+1
for fall and +2 for spring: freshman fall=1, freshman spring=2, junior
spring=6, senior spring=8. Do not require current_stage when the named target
term itself is sufficient.
"""
UNDERSTANDING_PROMPT += _EXPLICIT_TARGET_GUIDANCE
UNDERSTANDING_REVIEW_PROMPT += _EXPLICIT_TARGET_GUIDANCE

_TARGET_STAGE_GUIDANCE = """
Keep current_stage and target_stage distinct. current_stage is where the user
is now; target_stage is the named term whose courses or plan they request.
For a question saying all electives before junior spring were selected and
asking how to arrange junior spring, output target_stage={\"year\":3,
\"term\":\"\u4e0b\"}, leave current_stage null if not stated, and use
semester_relation=\"before_target_semester\". Never encode the preceding
semester (5) as the target; the requested junior-spring target is semester 6.
"""
UNDERSTANDING_PROMPT += _TARGET_STAGE_GUIDANCE
UNDERSTANDING_REVIEW_PROMPT += _TARGET_STAGE_GUIDANCE

def _stage(question: str) -> AcademicStage | None:
    match = re.search(r"大([一二三四])([上下])?", question)
    if not match:
        return None
    year = {"一": 1, "二": 2, "三": 3, "四": 4}[match.group(1)]
    return AcademicStage(year=year, term=match.group(2))


def _cohort(question: str) -> int | None:
    match = COHORT_RE.search(question)
    if match:
        return int(match.group(1))
    match = SHORT_COHORT_RE.search(question)
    if match:
        value = int(match.group(1))
        return 2000 + value if value <= 80 else 1900 + value
    return None


def _semesters(question: str) -> list[int]:
    zh = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
    values: list[int] = []
    for match in re.finditer(r"第([一二三四五六七八1-8])学期", question):
        token = match.group(1)
        values.append(int(token) if token.isdigit() else zh[token])
    return list(dict.fromkeys(values))


def _major_mention(question: str) -> str | None:
    aliases = (
        "计算机科学与技术",
        "计算机科学",
        "网络空间安全",
        "人工智能",
        "会计学",
        "财务管理",
        "金融学",
        "金融工程",
        "统计学",
        "保险学",
        "法学",
    )
    compact = question.replace(" ", "")
    for alias in aliases:
        if alias in compact:
            return alias
    if re.search(r"(?:AI专业|智能专业)", question, re.I):
        return "人工智能"
    if "计科" in question:
        return "计算机科学与技术"
    match = re.search(r"([\u4e00-\u9fffA-Za-z（）()]{2,32})专业", compact)
    return match.group(1) if match else None


def _completed_segment(question: str) -> list[str]:
    match = re.search(
        r"(?:已修|已经修|修完)(?:了|过)?(.+?)(?:，|。|；|;|还差|现在|接下来|应该|怎么|如何|$)",
        question,
    )
    if not match:
        return []
    segment = match.group(1).strip(" ，、")
    if not segment or re.search(r"全部|所有", segment):
        return []
    return [item.strip() for item in re.split(r"[、,，和及]", segment) if item.strip()]


def deterministic_understanding(question: str, **scope: Any) -> UnderstandingDraft:
    if EXPLICIT_GENERAL_CHAT_RE.fullmatch(question) or _explicit_general_task(question):
        domain = "general"
    else:
        domain = (
            "school"
            if SCHOOL_RE.search(question) or COURSE_CODE_RE.search(question)
            else "general"
        )
    if domain == "general":
        return UnderstandingDraft(
            domain="general",
            primary_intent="general_chat",
            requested_outputs=[],
            information_scope="unknown",
            confidence=0.94,
        )

    progress = bool(PROGRESS_RE.search(question)) and not bool(
        re.search(
            r"校历|寒假|暑假|放假|开学|返校|报到|行课|校车|班车|值班|营业|开放时间",
            question,
        )
    )
    promotion = bool(PROMOTION_RE.search(question))
    policy = bool(POLICY_RE.search(question) or POLICY_QUESTION_RE.search(question))
    graduation = bool(re.search(r"毕业.*(?:学分|要求)|(?:学分|要求).*毕业|各模块", question))
    if progress:
        intent = "progress_audit"
    elif promotion:
        intent = "promotion"
    elif policy:
        intent = "policy"
    elif graduation:
        intent = "graduation_requirement"
    elif re.search(r"课程|哪些课|什么课|选修|必修|学分|学时|代码", question):
        intent = "course_query"
    else:
        intent = "school_requirement"

    outputs: list[str] = []
    if re.search(r"哪些|什么课|课程|选修|必修|安排", question):
        outputs.append("course_list")
    if re.search(r"多少学分|总学分|学分毕业", question):
        outputs.append("credit_total")
    if "各模块" in question or re.search(r"模块.*学分", question):
        outputs.append("module_breakdown")
    if progress:
        outputs.extend(("remaining_courses", "remaining_credits"))
    if re.search(r"可行|大四不想|不排课|提前|怎么安排|如何安排", question):
        outputs.append("feasibility")
    if policy or promotion:
        outputs.append("policy_explanation")

    relation = None
    if "下学期" in question:
        relation = "next_semester"
    elif re.search(r"大四前|最后一年前", question):
        relation = "before_year_4"
    elif re.search(r"大四|最后一年", question):
        relation = "during_year_4"

    domains: list[str] = []
    if re.search(r"英语|外语", question):
        domains.append("foreign_language")
    if re.search(r"体育|篮球|足球|体能", question):
        domains.append("physical_education")
    if re.search(r"数学|微积分|代数|概率", question):
        domains.append("mathematics")
    if re.search(r"程序设计|编程", question):
        domains.append("programming")
    elif "计算机课" in question:
        domains.append("computing")
    if re.search(r"思想政治|思政|马克思|中国特色社会主义", question):
        domains.append("ideological_political")
    if re.search(r"军事教育|军事理论|军事技能|军训", question):
        domains.append("military_education")

    natures: list[str] = []
    if "必修" in question:
        natures.append("必修")
    if "选修" in question:
        natures.append("选修")
    modules: list[str] = []
    if "专业选修" in question:
        modules.append("专业选修课")
    elif "专业方向" in question:
        modules.append("专业方向课程")
    if re.search(r"实践环节|实践课程|实习|论文", question):
        modules.append("实践环节")
    completed_modules: list[str] = []
    if re.search(r"专业方向.*(?:全部|都|已经)?.*(?:修完|完成)|(?:修完|完成).*专业方向", question):
        completed_modules.append("专业方向课程")

    actual = bool(re.search(r"实际开课|教务系统.*开课|本学期能选|下学期能选", question))
    info_scope = "actual_offerings" if actual else "curriculum_plan"
    cohort = _cohort(question) or scope.get("cohort")
    if isinstance(cohort, str) and cohort.isdigit():
        cohort = int(cohort)
    return UnderstandingDraft(
        domain="school",
        primary_intent=intent,
        requested_outputs=list(dict.fromkeys(outputs)),
        college_mention=scope.get("college"),
        major_mention=_major_mention(question) or scope.get("major"),
        cohort_mention=cohort,
        current_stage=_stage(question),
        explicit_semesters=_semesters(question),
        target_relation=relation,
        course_codes=[value.upper() for value in COURSE_CODE_RE.findall(question)],
        subject_domain_mentions=domains,
        course_nature_mentions=natures,
        course_module_mentions=modules,
        completed_course_mentions=_completed_segment(question),
        completed_module_claims=completed_modules,
        goal_mentions=[value for value in ("avoid_year_4_courses" if re.search(r"大四不想上课|大四不排课", question) else None,) if value],
        information_scope=info_scope,
        confidence=0.88,
    )


class QuestionUnderstandingService:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client

    @staticmethod
    def _validated(raw: str) -> UnderstandingDraft:
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
        value = json.loads(clean)
        # LLMs commonly use null for an empty array.  Preserve strict field
        # names and enum validation while canonicalizing this harmless shape
        # difference instead of discarding the entire semantic draft.
        if isinstance(value, dict):
            for name, field in UnderstandingDraft.model_fields.items():
                if value.get(name) is None and field.default_factory is not None:
                    value[name] = field.default_factory()
            for claim in value.get("completed_scope_claims") or []:
                if isinstance(claim, dict):
                    claim["course_natures"] = claim.get("course_natures") or []
                    claim["course_modules"] = claim.get("course_modules") or []

        forbidden = {"sql", "table", "url", "operation", "operations", "where_clause"}
        if not isinstance(value, dict) or forbidden & set(value):
            raise ValueError("forbidden planner fields")
        draft = UnderstandingDraft.model_validate(value)
        return draft.model_copy(update={"parser": "llm"})

    @staticmethod
    def _apply_scope(draft: UnderstandingDraft, scope: dict[str, Any]) -> UnderstandingDraft:
        values = draft.model_dump()
        if scope.get("college"):
            values["college_mention"] = scope["college"]
        if scope.get("cohort") and str(scope["cohort"]).isdigit():
            values["cohort_mention"] = int(scope["cohort"])
        if scope.get("major") and not values.get("major_mention"):
            values["major_mention"] = scope["major"]
        return UnderstandingDraft.model_validate(values)

    def understand(self, question: str, **scope: Any) -> UnderstandingDraft:
        fallback = deterministic_understanding(question, **scope)
        if (
            self.client is None
            or EXPLICIT_GENERAL_CHAT_RE.fullmatch(question)
            or _explicit_general_task(question)
        ):
            return fallback
        schema = UnderstandingDraft.model_json_schema()
        prompt = json.dumps(
            {"question": question, "scope": scope, "json_schema": schema},
            ensure_ascii=False,
        )
        try:
            initial = self._validated(self.client.generate(UNDERSTANDING_PROMPT, prompt))
        except Exception:
            return fallback

        review_prompt = json.dumps(
            {
                "original_question": question,
                "scope": scope,
                "candidate_draft": initial.model_dump(),
                "json_schema": schema,
            },
            ensure_ascii=False,
        )
        try:
            reviewed = self._validated(
                self.client.generate(UNDERSTANDING_REVIEW_PROMPT, review_prompt)
            )
        except Exception:
            reviewed = initial
        reviewed = self._apply_scope(reviewed, scope)
        if fallback.domain == "school" and reviewed.domain != "school":
            reviewed = reviewed.model_copy(
                update={
                    "domain": "school",
                    "primary_intent": fallback.primary_intent,
                    "requested_outputs": fallback.requested_outputs,
                    "confidence": min(reviewed.confidence, fallback.confidence),
                }
            )
        return reviewed


__all__ = [
    "QuestionUnderstandingService",
    "UNDERSTANDING_PROMPT",
    "deterministic_understanding",
]
