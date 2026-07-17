import json

from swufe_rag.query_understanding import QuestionUnderstandingService


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
