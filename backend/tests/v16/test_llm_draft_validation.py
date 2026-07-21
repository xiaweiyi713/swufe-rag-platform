import json

from swufe_rag.query_understanding import QuestionUnderstandingService
from swufe_rag.query_semantics import QuestionUnderstandingService as SemanticService


class UnexpectedLLMClient:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        raise AssertionError("pure greetings must not invoke question understanding")


class GeneralizingLLMClient:
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        return json.dumps(
            {
                "domain": "general",
                "primary_intent": "general_chat",
                "requested_outputs": [],
                "information_scope": "unknown",
                "confidence": 0.99,
            },
            ensure_ascii=False,
        )


class FixedDraftClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        return json.dumps(self.payload, ensure_ascii=False)


def test_pure_greeting_ignores_school_scope_and_skips_llm_understanding():
    client = UnexpectedLLMClient()

    draft = QuestionUnderstandingService(client).understand(
        "你好",
        college="计算机与人工智能学院",
        cohort="2023",
        major="人工智能专业",
    )

    assert draft.domain == "general"
    assert draft.primary_intent == "general_chat"
    assert client.calls == 0


def test_llm_draft_accepts_null_for_empty_list_without_losing_scope_claim():
    raw = json.dumps(
        {
            "domain": "school",
            "primary_intent": "progress_audit",
            "requested_outputs": ["remaining_courses", "remaining_credits"],
            "major_mention": "AI",
            "cohort_mention": 2023,
            "current_stage": {"year": 3, "term": "下"},
            "explicit_semesters": None,
            "completed_scope_claims": [
                {
                    "scope": "all_matching_courses",
                    "semester_relation": "before_current_semester",
                    "course_natures": ["选修"],
                    "course_modules": None,
                    "status": "selected",
                }
            ],
            "information_scope": "curriculum_plan",
            "confidence": 0.9,
        },
        ensure_ascii=False,
    )

    draft = QuestionUnderstandingService._validated(raw)

    assert draft.parser == "llm"
    assert draft.explicit_semesters == []
    assert len(draft.completed_scope_claims) == 1
    assert draft.completed_scope_claims[0].semester_relation == "before_current_semester"
    assert draft.completed_scope_claims[0].course_modules == []


def test_explicit_general_task_skips_llm_understanding_but_not_general_generation():
    client = UnexpectedLLMClient()

    draft = QuestionUnderstandingService(client).understand(
        "帮我写一个Python选课系统"
    )

    assert draft.domain == "general"
    assert draft.primary_intent == "general_chat"
    assert client.calls == 0


def test_llm_cannot_downgrade_explicit_school_fact_to_general_chat():
    draft = QuestionUnderstandingService(GeneralizingLLMClient()).understand(
        "西财推免需要满足什么条件？"
    )

    assert draft.domain == "school"
    assert draft.primary_intent == "promotion"


def test_semantic_repair_keeps_degree_conditions_and_private_grades_on_policy_path():
    wrong = {
        "domain": "school",
        "primary_intent": "graduation_requirement",
        "requested_outputs": ["course_list", "credit_total"],
        "information_scope": "curriculum_plan",
        "confidence": 0.9,
    }
    for question in (
        "拿学士学位需要满足什么条件？",
        "帮我查一下我的期末成绩",
    ):
        draft = SemanticService(FixedDraftClient(wrong)).understand(
            question,
            college="统计学院",
            cohort="2023",
            major="经济统计学专业",
        )
        assert draft.primary_intent == "policy"
        assert draft.requested_outputs == ["policy_explanation"]
        assert draft.information_scope == "school_policy"


def test_semantic_repair_preserves_explicit_completed_module_claim():
    wrong = {
        "domain": "school",
        "primary_intent": "progress_audit",
        "requested_outputs": ["course_list", "remaining_credits"],
        "current_stage": {"year": 3, "term": "下"},
        "completed_module_claims": [],
        "completed_scope_claims": [
            {
                "scope": "all_matching_courses",
                "semester_relation": "before_current_semester",
                "course_natures": ["选修"],
                "course_modules": ["专业方向课"],
                "status": "completed",
            }
        ],
        "information_scope": "curriculum_plan",
        "confidence": 0.9,
    }
    draft = SemanticService(FixedDraftClient(wrong)).understand(
        "专业方向课已经全部修完，现在应该怎么安排大三下课程？",
        college="计算机与人工智能学院",
        cohort="2023",
        major="人工智能专业",
    )

    assert draft.primary_intent == "progress_audit"
    assert draft.completed_module_claims == ["专业方向课"]
    assert draft.completed_scope_claims == []
    assert draft.current_stage is not None
    assert draft.current_stage.year == 3
    assert draft.current_stage.term == "下"
