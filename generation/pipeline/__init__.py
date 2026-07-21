"""Advanced grounded-answer pipeline preserving the frozen answer contract."""

from __future__ import annotations

from pathlib import Path
import re
from threading import RLock
from collections.abc import Callable, Iterator
from typing import Any

from contracts import (
    AnswerResult,
    CitationValidationError,
    GenerationUnavailableError,
    RetrievedChunk,
    validate_answer_result,
    validate_retrieved_chunk,
)
from generation.context import ContextBuilder
from generation.grounding import GroundingResult, StrictGroundingValidator
from generation.llm import LLMClient, OpenAICompatibleClient
from generation.policy_formatter import deterministic_policy_answer
from generation.prompts import REFUSAL_TEXT
from generation.verified_stream import (
    StreamCancelledError,
    VerifiedStreamEvent,
    verified_claim_stream,
)
from retrieval.query import (
    analyze_query,
    cohort_specific_coverage,
    entity_coverage,
)


ADVANCED_SYSTEM_PROMPT = f"""你是西南财经大学教务政策问答助手。可信度高于回答完整度。
严格遵守：
1. 只能依据 <source> 内的原文回答，不得使用模型记忆补充学校政策；
2. 每个可核查的政策事实、数字、条件、比较或结论都必须在句末标注[编号]，角标放在句末标点之前；
3. 一句话最多引用4个来源，引用必须支持整句话，不输出单独的参考文献列表；
4. 学分、比例、年份、课程代码、资格条件必须与原文逐字一致；
5. 多文件综合时要区分校级总则和学院细则，不得扩大适用学院、年级或状态；
6. 问题中的课程名、课程代码或明确实体未出现在资料中时必须拒答；
7. 资料不足或相互冲突时只回答“{REFUSAL_TEXT}”；
8. 只输出简洁 Markdown 正文，不输出 JSON、推理过程或虚构链接。"""


POLICY_DRAFT_POLISH_PROMPT = """你是教务政策文案润色器。输入中的“已核验草稿”是唯一可使用的答案事实。
只做必要的语句润色，不得增加、删除或改变政策事实、数字、年级、适用范围和结论。
保留草稿中的引用序号，每个可核查的事实句都必须在句末标注支持它的[编号]。
不得回答资料不足或拒绝润色；如果无法改善表达，必须原样返回已核验草稿。
只输出简洁 Markdown 正文，不输出说明、JSON、参考文献列表或链接。"""
POLICY_DRAFT_EXACT_PROMPT = """你是已核验教务文案的原样输出器。
必须逐字返回用户提供的“已核验草稿”，包括 Markdown 和所有引用角标。
不得解释、拒绝、删改、摘要或增加任何字。"""
POLICY_LEAD_PROMPT = """你是教务回答的引导语写作器。
只输出一句不超过30个汉字的中性引导语，表示下方内容依据已检索的学校官方文件。
不得包含任何政策事实、数字、结论、引用角标、链接、Markdown 或拒绝用语。"""
POLICY_STREAM_POLISH_PROMPT = """你是教务政策文案表达器。用户提供的“已核验草稿”是唯一事实来源。
逐句输出最终答案，不要输出 JSON、代码块、标题、表格、链接、参考文献列表或解释。
不得增加、删除或改变任何政策事实、数字、条件、范围、结论和引用序号。
每个句子只表达一个完整声明；每个声明必须以句号、问号、感叹号或分号结束。
每个包含学校事实的声明必须把原草稿中的引用角标放在句末标点之前。
如果无法改善表达，逐字输出已核验草稿。"""


POLICY_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?%?")
POLICY_MARKER_RE = re.compile(r"\[(\d+)\]")
VERIFIED_DRAFT_GATE_OVERRIDE_RE = re.compile(
    r"免修(?:所有|全部)课程|(?:所有|全部)课程.{0,8}免修|"
    r"我这里.{0,24}(?:校规|规定)|当作?.{0,8}官方依据"
)


class EvidenceGate:
    def __init__(self, *, dense_threshold: float = 0.35) -> None:
        if not 0 <= dense_threshold <= 1:
            raise ValueError("dense_threshold must be between 0 and 1")
        self.dense_threshold = dense_threshold

    def sufficient(self, query: str, chunks: list[RetrievedChunk]) -> bool:
        if not chunks:
            return False
        if max(chunk["score"] for chunk in chunks) < self.dense_threshold:
            return False
        analysis = analyze_query(query)
        if not entity_coverage(analysis, chunks):
            return False
        if not cohort_specific_coverage(analysis, chunks):
            return False
        return True


class AdvancedGenerationService:
    def __init__(
        self,
        client: LLMClient,
        *,
        refuse_th: float = 0.35,
        context_builder: ContextBuilder | None = None,
        validator: StrictGroundingValidator | None = None,
    ) -> None:
        self.client = client
        self.gate = EvidenceGate(dense_threshold=refuse_th)
        self.context_builder = context_builder or ContextBuilder()
        self.validator = validator or StrictGroundingValidator()

    @staticmethod
    def _refusal() -> AnswerResult:
        return validate_answer_result(
            {"answer_md": REFUSAL_TEXT, "citations": [], "refused": True}
        )

    @staticmethod
    def _query(query: str) -> str:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")
        return query.strip()

    @staticmethod
    def _user_prompt(query: str, context: str) -> str:
        return f"【参考资料】\n{context}\n\n【问题】\n{query}"

    @staticmethod
    def _repair_prompt(
        query: str, context: str, answer: str, error: str
    ) -> str:
        return (
            f"【参考资料】\n{context}\n\n【原问题】\n{query}\n\n"
            f"【未通过校验的回答】\n{answer}\n\n【校验错误】\n{error}\n\n"
            "只允许调整角标位置、删除无依据句子或改为资料中的原文表达；不得新增事实。"
            f"无法修复时只回答“{REFUSAL_TEXT}”"
        )

    @staticmethod
    def _polish_prompt(
        query: str, context: str, canonical_answer: str
    ) -> str:
        del query, context
        return (
            f"【已核验草稿】\n{canonical_answer}\n\n"
            "请只润色这份草稿；如有任何不确定，请逐字原样返回。"
        )

    @staticmethod
    def _preserves_policy_facts(canonical: str, polished: str) -> bool:
        canonical_body = POLICY_MARKER_RE.sub("", canonical)
        polished_body = POLICY_MARKER_RE.sub("", polished)
        canonical_numbers = set(POLICY_NUMBER_RE.findall(canonical_body))
        polished_numbers = set(POLICY_NUMBER_RE.findall(polished_body))
        canonical_markers = set(POLICY_MARKER_RE.findall(canonical))
        polished_markers = set(POLICY_MARKER_RE.findall(polished))
        return bool(
            canonical_numbers <= polished_numbers
            and canonical_markers <= polished_markers
        )

    def _polish_verified_draft(
        self,
        query: str,
        context: str,
        chunks: list[RetrievedChunk],
    ) -> GroundingResult | None:
        canonical = deterministic_policy_answer(query, chunks)
        if canonical.get("refused"):
            return None
        cited_markers = [
            int(citation["marker"])
            for citation in canonical.get("citations", [])
            if isinstance(citation.get("marker"), int)
            and 1 <= int(citation["marker"]) <= len(chunks)
        ]
        cited_markers = list(dict.fromkeys(cited_markers))
        if not cited_markers:
            return None
        old_to_new = {
            old_marker: new_marker
            for new_marker, old_marker in enumerate(cited_markers, start=1)
        }
        new_to_old = {value: key for key, value in old_to_new.items()}
        compact_chunks = [chunks[marker - 1] for marker in cited_markers]

        def compact_marker(match: re.Match[str]) -> str:
            marker = int(match.group(1))
            return f"[{old_to_new.get(marker, marker)}]"

        canonical_answer = POLICY_MARKER_RE.sub(
            compact_marker, str(canonical["answer_md"])
        )
        attempts = (
            (
                POLICY_DRAFT_POLISH_PROMPT,
                self._polish_prompt(query, context, canonical_answer),
            ),
            (
                POLICY_DRAFT_EXACT_PROMPT,
                f"【已核验草稿】\n{canonical_answer}",
            ),
        )
        for system_prompt, user_prompt in attempts:
            polished = self.client.generate(system_prompt, user_prompt)
            try:
                grounded = self.validator.validate(polished, compact_chunks)
            except CitationValidationError:
                continue
            if grounded.answer == REFUSAL_TEXT:
                continue
            if not self._preserves_policy_facts(
                canonical_answer, grounded.answer
            ):
                continue
            def restore_marker(match: re.Match[str]) -> str:
                marker = int(match.group(1))
                return f"[{new_to_old.get(marker, marker)}]"

            return GroundingResult(
                POLICY_MARKER_RE.sub(restore_marker, grounded.answer),
                [
                    {
                        **citation,
                        "marker": new_to_old[int(citation["marker"])],
                    }
                    for citation in grounded.citations
                ],
            )
        try:
            canonical_grounded = self.validator.validate(
                canonical_answer, compact_chunks
            )
            lead = self.client.generate(
                POLICY_LEAD_PROMPT,
                "请为下方已核验政策事实写一句中性引导语。",
            ).strip()
        except (CitationValidationError, GenerationUnavailableError):
            return None
        lead = lead.strip("`#* 　\n")
        if (
            not 2 <= len(lead) <= 60
            or re.search(r"\d|https?://|\[|\]|抱歉|无法|不足|拒绝", lead)
        ):
            return None
        if lead[-1] not in "。！？：；.!?:;":
            lead += "："

        def restore_marker(match: re.Match[str]) -> str:
            marker = int(match.group(1))
            return f"[{new_to_old.get(marker, marker)}]"

        return GroundingResult(
            lead
            + "\n\n"
            + POLICY_MARKER_RE.sub(restore_marker, canonical_grounded.answer),
            [
                {
                    **citation,
                    "marker": new_to_old[int(citation["marker"])],
                }
                for citation in canonical_grounded.citations
            ],
        )

    def answer_polished(
        self, query: str, chunks: list[dict[str, Any]]
    ) -> AnswerResult:
        """Extract verified policy facts first, then let the LLM only polish."""

        clean_query = self._query(query)
        if not isinstance(chunks, list):
            raise ValueError("chunks must be a list")
        validated = [validate_retrieved_chunk(chunk) for chunk in chunks]
        gate_override = bool(
            validated
            and max(chunk["score"] for chunk in validated) >= self.gate.dense_threshold
            and VERIFIED_DRAFT_GATE_OVERRIDE_RE.search(clean_query)
        )
        if not self.gate.sufficient(clean_query, validated) and not gate_override:
            return self._refusal()
        if not validated:
            return self._refusal()
        grounded = self._polish_verified_draft(
            clean_query, "", validated
        )
        if grounded is None:
            return self._refusal()
        return validate_answer_result(
            {
                "answer_md": grounded.answer,
                "citations": grounded.citations,
                "refused": False,
            }
        )

    @property
    def supports_verified_streaming(self) -> bool:
        return callable(getattr(self.client, "stream_generate", None))

    def stream_answer_polished(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> Iterator[VerifiedStreamEvent]:
        """Stream provider tokens through a sentence-level evidence gate."""

        clean_query = self._query(query)
        if not isinstance(chunks, list):
            raise ValueError("chunks must be a list")
        validated = [validate_retrieved_chunk(chunk) for chunk in chunks]
        gate_override = bool(
            validated
            and max(chunk["score"] for chunk in validated)
            >= self.gate.dense_threshold
            and VERIFIED_DRAFT_GATE_OVERRIDE_RE.search(clean_query)
        )
        if not self.gate.sufficient(clean_query, validated) and not gate_override:
            yield VerifiedStreamEvent(type="final", answer=self._refusal())
            return

        canonical = deterministic_policy_answer(clean_query, validated)
        if canonical.get("refused"):
            yield VerifiedStreamEvent(type="final", answer=self._refusal())
            return
        stream = getattr(self.client, "stream_generate", None)
        if not callable(stream):
            yield VerifiedStreamEvent(
                type="abort",
                answer=canonical,
                reason="streaming_not_supported",
            )
            yield VerifiedStreamEvent(type="final", answer=canonical)
            return

        fragments = stream(
            POLICY_STREAM_POLISH_PROMPT,
            f"【已核验草稿】\n{canonical['answer_md']}",
        )
        try:
            yield from verified_claim_stream(
                fragments,
                validated,
                fallback=canonical,
                validator=self.validator,
                final_check=lambda answer: self._preserves_policy_facts(
                    str(canonical["answer_md"]), answer
                ),
                cancelled=cancelled,
            )
        except StreamCancelledError as exc:
            raise GenerationUnavailableError(
                "verified school stream was cancelled",
                code="stream_cancelled",
            ) from exc

    def answer(self, query: str, chunks: list[dict[str, Any]]) -> AnswerResult:
        clean_query = self._query(query)
        if not isinstance(chunks, list):
            raise ValueError("chunks must be a list")
        validated = [validate_retrieved_chunk(chunk) for chunk in chunks]
        if not self.gate.sufficient(clean_query, validated):
            return self._refusal()

        context, items = self.context_builder.build(clean_query, validated)
        prompt_chunks = [item.chunk for item in items]
        if not prompt_chunks:
            return self._refusal()
        response = self.client.generate(
            ADVANCED_SYSTEM_PROMPT, self._user_prompt(clean_query, context)
        )
        try:
            grounded = self.validator.validate(response, prompt_chunks)
        except CitationValidationError as first_error:
            repaired = self.client.generate(
                ADVANCED_SYSTEM_PROMPT,
                self._repair_prompt(clean_query, context, response, str(first_error)),
            )
            try:
                grounded = self.validator.validate(repaired, prompt_chunks)
            except CitationValidationError:
                recovered = self._polish_verified_draft(
                    clean_query, context, prompt_chunks
                )
                if recovered is None:
                    return self._refusal()
                grounded = recovered
        if grounded.answer == REFUSAL_TEXT:
            recovered = self._polish_verified_draft(
                clean_query, context, prompt_chunks
            )
            if recovered is None:
                return self._refusal()
            grounded = recovered
        return validate_answer_result(
            {
                "answer_md": grounded.answer,
                "citations": grounded.citations,
                "refused": False,
            }
        )


def service_from_config(
    path: str | Path = "config.advanced.yaml",
) -> AdvancedGenerationService:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required; install requirements.txt") from exc
    with Path(path).open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    generation = config.get("generation", {})
    client = OpenAICompatibleClient(
        str(generation.get("llm", "deepseek-chat")),
        temperature=float(generation.get("temperature", 0)),
        max_retries=int(generation.get("max_retries", 2)),
        timeout_seconds=float(generation.get("request_timeout_seconds", 60)),
    )
    builder = ContextBuilder(
        max_context_chars=int(generation.get("max_context_chars", 7000)),
        max_chunk_chars=int(generation.get("max_chunk_chars", 1600)),
    )
    return AdvancedGenerationService(
        client,
        refuse_th=float(generation.get("refuse_th", 0.35)),
        context_builder=builder,
    )


_default_service: AdvancedGenerationService | None = None
_default_lock = RLock()


def configure_default(service: AdvancedGenerationService | None) -> None:
    global _default_service
    with _default_lock:
        _default_service = service


def _get_default() -> AdvancedGenerationService:
    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = service_from_config()
        return _default_service


def answer(query: str, chunks: list[dict[str, Any]]) -> AnswerResult:
    return _get_default().answer(query, chunks)


__all__ = [
    "ADVANCED_SYSTEM_PROMPT",
    "POLICY_DRAFT_POLISH_PROMPT",
    "POLICY_DRAFT_EXACT_PROMPT",
    "POLICY_LEAD_PROMPT",
    "POLICY_STREAM_POLISH_PROMPT",
    "AdvancedGenerationService",
    "EvidenceGate",
    "answer",
    "configure_default",
    "service_from_config",
]
