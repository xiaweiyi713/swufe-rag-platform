from __future__ import annotations

import json
import re

from academic_audit.database import AcademicDatabase
from academic_audit.execution_service import execute_plan
from generation.answer_presenter import AnswerPresenter
from storage.metadata_db import MetadataDB
from swufe_rag.normalization_service import normalize_query
from swufe_rag.query_understanding import deterministic_understanding
from swufe_rag.tool_planner import build_execution_plan


class GraduationLeadClient:
    def __init__(self, summary: str) -> None:
        self.summary = summary

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        return json.dumps(
            {
                "summary": self.summary,
                "explanations": [],
                "warnings": [],
                "clarification_question": None,
            },
            ensure_ascii=False,
        )


class RepetitiveGraduationClient:
    def __init__(self) -> None:
        self.user_payload: dict = {}

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.user_payload = json.loads(user_prompt)
        return json.dumps(
            {
                "summary": "根据2023级培养方案，经济统计学专业毕业最低需要修满166学分。",
                "explanations": [
                    {
                        "text": "各模块最低学分要求为：通识教育基础课67学分、大学科基础课22学分。",
                        "evidence_ids": ["E1"],
                    }
                ],
                "warnings": ["各模块学分独立计算。"],
                "clarification_question": None,
            },
            ensure_ascii=False,
        )


class CapturingLeadClient(GraduationLeadClient):
    def __init__(self, summary: str) -> None:
        super().__init__(summary)
        self.user_payload: dict = {}

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.user_payload = json.loads(user_prompt)
        return super().generate(system_prompt, user_prompt)


class RepairingMultiRowLeadClient:
    def __init__(self) -> None:
        self.user_payloads: list[dict] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.user_payloads.append(json.loads(user_prompt))
        summary = (
            "第6学期必修课共8门，合计17学分，安排在专业方向课模块。"
            if len(self.user_payloads) == 1
            else "根据培养方案，相关明细及其适用范围请见下表。"
        )
        return json.dumps(
            {
                "summary": summary,
                "explanations": [],
                "warnings": [],
                "clarification_question": None,
            },
            ensure_ascii=False,
        )


def test_llm_writes_the_lead_and_program_keeps_tables_without_duplicate_lead() -> None:
    question = "人工智能专业2023级毕业需要多少学分？"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    metadata = MetadataDB("data/metadata.sqlite3")
    try:
        draft = deterministic_understanding(
            question,
            college="计算机与人工智能学院",
            cohort="2023",
            major="人工智能专业",
        )
        normalized = normalize_query(draft, question, database=database)
        plan = build_execution_plan(normalized)
        packet = execute_plan(plan, database=database, metadata=metadata)
        result = AnswerPresenter(
            GraduationLeadClient(
                "根据当前培养方案，2023级人工智能专业毕业需要修满165学分。"
            )
        ).present(plan, packet)
    finally:
        metadata.close()
        database.close()

    assert result.llm_called is True
    assert result.llm_accepted is True
    assert result.final_output_source == "llm"
    assert result.answer_md.startswith(
        "根据当前培养方案，2023级人工智能专业毕业需要修满165学分。"
    )
    assert "### 培养方案模块要求" in result.answer_md
    assert "| 模块 | 最低学分 | 说明 |" in result.answer_md
    assert result.answer_md.count("毕业需要修满165学分") == 1


def test_economic_statistics_total_is_allowed_in_llm_lead() -> None:
    question = "毕业需要修满多少学分？"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    metadata = MetadataDB("data/metadata.sqlite3")
    try:
        draft = deterministic_understanding(
            question,
            college="统计学院",
            cohort="2023",
            major="经济统计学专业",
        )
        normalized = normalize_query(draft, question, database=database)
        plan = build_execution_plan(normalized)
        packet = execute_plan(plan, database=database, metadata=metadata)
        result = AnswerPresenter(
            GraduationLeadClient(
                "根据2023级培养方案，经济统计学专业毕业最低需要修满166学分。"
            )
        ).present(plan, packet)
    finally:
        metadata.close()
        database.close()

    assert result.llm_accepted is True
    assert result.answer_md.startswith(
        "根据2023级培养方案，经济统计学专业毕业最低需要修满166学分。"
    )
    assert "### 培养方案模块要求" in result.answer_md
    assert "2023级经济统计学专业的毕业最低学分为" not in result.answer_md


def test_graduation_lead_does_not_repeat_program_module_table() -> None:
    question = "毕业需要修满多少学分？"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    metadata = MetadataDB("data/metadata.sqlite3")
    client = RepetitiveGraduationClient()
    try:
        draft = deterministic_understanding(
            question,
            college="统计学院",
            cohort="2023",
            major="经济统计学专业",
        )
        normalized = normalize_query(draft, question, database=database)
        plan = build_execution_plan(normalized)
        packet = execute_plan(plan, database=database, metadata=metadata)
        result = AnswerPresenter(client).present(plan, packet)
    finally:
        metadata.close()
        database.close()

    lead, details = result.answer_md.split("### 培养方案模块要求", 1)
    assert "166学分" in lead
    assert "各模块最低学分要求" not in lead
    assert "通识教育基础课67学分" not in lead
    assert "各模块学分独立计算" not in lead
    assert "| 模块 | 最低学分 | 说明 |" in details
    assert "| （一）通识教育基础课 | 67 |" in details

    prompt_packet = client.user_payload["evidence_packet"]
    assert prompt_packet["requirements"] == []
    assert prompt_packet["courses"] == []
    assert [fact["field"] for fact in prompt_packet["facts"]] == [
        "graduation_min_credits"
    ]
    assert prompt_packet["facts"][0]["value"] == 166
    assert client.user_payload["presentation_instruction"].startswith(
        "只用一个自然段直接回答毕业最低总学分"
    )


def test_multi_course_table_rows_are_hidden_from_the_lead_writer() -> None:
    question = "大三下有哪些必修课？"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    metadata = MetadataDB("data/metadata.sqlite3")
    client = CapturingLeadClient("根据培养方案，第6学期课程分为明确安排和跨学期完成范围，详见下表。")
    try:
        draft = deterministic_understanding(
            question,
            college="计算机与人工智能学院",
            cohort="2023",
            major="人工智能专业",
        )
        normalized = normalize_query(draft, question, database=database)
        plan = build_execution_plan(normalized)
        packet = execute_plan(plan, database=database, metadata=metadata)
        result = AnswerPresenter(client).present(plan, packet)
    finally:
        metadata.close()
        database.close()

    assert result.llm_accepted is True
    assert "### 2023级人工智能专业第6学期明确安排课程" in result.answer_md
    assert client.user_payload["evidence_packet"]["courses"] == []
    assert client.user_payload["evidence_packet"]["operation_results"] == []
    assert client.user_payload["presentation_instruction"].startswith(
        "summary 必须逐字等于"
    )
    assert "相关明细及其适用范围请见下表" in client.user_payload[
        "presentation_instruction"
    ]


def test_invented_multi_row_aggregate_is_rewritten_before_display() -> None:
    question = "2023级经济统计学专业大三下有哪些必修课？"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    metadata = MetadataDB("data/metadata.sqlite3")
    client = RepairingMultiRowLeadClient()
    try:
        draft = deterministic_understanding(
            question,
            college="统计学院",
            cohort="2023",
            major="经济统计学专业",
        )
        normalized = normalize_query(draft, question, database=database)
        plan = build_execution_plan(normalized)
        packet = execute_plan(plan, database=database, metadata=metadata)
        result = AnswerPresenter(client).present(plan, packet)
    finally:
        metadata.close()
        database.close()

    lead = result.answer_md.split("###", 1)[0]
    assert result.llm_accepted is True
    assert len(client.user_payloads) == 2
    assert "8门" not in lead
    assert "17学分" not in lead
    assert "专业方向课" not in lead
    assert "相关明细及其适用范围请见下表" in lead


class EchoExpectedSummaryClient:
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        payload = json.loads(user_prompt)
        match = re.search(
            r"summary 必须逐字等于：“(.+)”",
            payload["presentation_instruction"],
        )
        assert match is not None
        return json.dumps(
            {
                "summary": match.group(1),
                "explanations": [],
                "warnings": [],
                "clarification_question": None,
            },
            ensure_ascii=False,
        )


def test_single_requirement_table_keeps_llm_direct_answer() -> None:
    question = "2023级人工智能专业专业方向课最低多少学分？"
    database = AcademicDatabase("data/academic_v2.sqlite3")
    metadata = MetadataDB("data/metadata.sqlite3")
    try:
        draft = deterministic_understanding(
            question,
            college="计算机与人工智能学院",
            cohort="2023",
            major="人工智能专业",
        )
        normalized = normalize_query(draft, question, database=database)
        plan = build_execution_plan(normalized)
        packet = execute_plan(plan, database=database, metadata=metadata)
        result = AnswerPresenter(EchoExpectedSummaryClient()).present(plan, packet)
    finally:
        metadata.close()
        database.close()

    lead = result.answer_md.split("###", 1)[0]
    assert result.final_output_source == "llm"
    assert "2023级人工智能专业的（四）专业方向课最低要求为18学分" in lead
