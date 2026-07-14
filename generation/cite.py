"""Citation parsing, grounding validation and exact-source quote mapping."""

from __future__ import annotations

from collections import OrderedDict
import re
from typing import Iterable

from contracts import Citation, CitationValidationError, RetrievedChunk
from generation.prompts import REFUSAL_TEXT


MARKER_RE = re.compile(r"\[(\d+)\]")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])\s*|\n+")
PASSAGE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])|\n+")
STOP_WORDS = {
    "的",
    "了",
    "和",
    "与",
    "及",
    "或",
    "是",
    "为",
    "在",
    "按",
    "由",
    "应",
    "须",
    "可以",
    "规定",
    "学生",
    "申请人",
    "根据",
    "其中",
}


def extract_markers(text: str) -> list[int]:
    return [int(value) for value in MARKER_RE.findall(text)]


def _content_tokens(text: str) -> set[str]:
    cleaned = MARKER_RE.sub("", text).lower()
    tokens = set(re.findall(r"[a-z]+\d*|\d+(?:\.\d+)?%?", cleaned))
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", cleaned)
    try:
        import jieba
    except ImportError:
        for run in chinese_runs:
            tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    else:
        for run in chinese_runs:
            tokens.update(token.strip() for token in jieba.lcut(run) if token.strip())
    return {token for token in tokens if token not in STOP_WORDS and token.strip()}


def _passages(text: str) -> list[str]:
    passages = [part.strip() for part in PASSAGE_SPLIT_RE.split(text) if part.strip()]
    return passages or [text]


def _best_passage(claim: str, chunk_text: str) -> tuple[str, int, float]:
    claim_tokens = _content_tokens(claim)
    claim_numbers = set(NUMBER_RE.findall(MARKER_RE.sub("", claim)))
    best = (chunk_text, 0, 0.0)
    for passage in _passages(chunk_text):
        passage_tokens = _content_tokens(passage)
        overlap = len(claim_tokens & passage_tokens)
        union = len(claim_tokens | passage_tokens)
        jaccard = overlap / union if union else 0.0
        number_bonus = len(claim_numbers & set(NUMBER_RE.findall(passage)))
        candidate = (passage, overlap + number_bonus * 2, jaccard)
        if (candidate[1], candidate[2], -len(candidate[0])) > (
            best[1],
            best[2],
            -len(best[0]),
        ):
            best = candidate
    return best


def _is_exempt_sentence(sentence: str) -> bool:
    cleaned = MARKER_RE.sub("", sentence).strip(" \t。！？!?；;：:")
    if not cleaned:
        return True
    if REFUSAL_TEXT.rstrip("。") in cleaned:
        return True
    if cleaned.startswith(("建议咨询", "以下条款仅供参考", "仅供参考")):
        return True
    return False


def _validate_sentence(sentence: str, chunks: list[RetrievedChunk]) -> None:
    if _is_exempt_sentence(sentence):
        return
    markers = extract_markers(sentence)
    if not markers:
        raise CitationValidationError(f"factual sentence has no citation: {sentence}")
    invalid = [marker for marker in markers if marker < 1 or marker > len(chunks)]
    if invalid:
        raise CitationValidationError(f"citation marker is out of range: {invalid[0]}")

    cited_chunks = [chunks[marker - 1] for marker in dict.fromkeys(markers)]
    cleaned = MARKER_RE.sub("", sentence)
    numbers = set(NUMBER_RE.findall(cleaned))
    combined_source = "\n".join(chunk["text"] for chunk in cited_chunks)
    missing_numbers = sorted(number for number in numbers if number not in combined_source)
    if missing_numbers:
        raise CitationValidationError(
            f"numbers are absent from cited source: {', '.join(missing_numbers)}"
        )

    if not numbers:
        best_overlap = 0
        best_jaccard = 0.0
        for chunk in cited_chunks:
            _, overlap, jaccard = _best_passage(cleaned, chunk["text"])
            best_overlap = max(best_overlap, overlap)
            best_jaccard = max(best_jaccard, jaccard)
        if best_overlap < 2 or best_jaccard < 0.1:
            raise CitationValidationError("cited source is not sufficiently related to the claim")


def _claim_sentences(answer: str) -> list[str]:
    return [part.strip() for part in SENTENCE_SPLIT_RE.split(answer) if part.strip()]


def _quote_for_marker(
    marker: int, claims: Iterable[str], chunk: RetrievedChunk, max_length: int = 300
) -> str:
    related_claims = [claim for claim in claims if marker in extract_markers(claim)]
    query = " ".join(related_claims)
    passage, _, _ = _best_passage(query, chunk["text"])
    if len(passage) <= max_length:
        return passage
    tokens = _content_tokens(query)
    positions = [passage.find(token) for token in tokens if passage.find(token) >= 0]
    center = min(positions) if positions else 0
    start = max(0, min(center - max_length // 3, len(passage) - max_length))
    return passage[start : start + max_length]


def validate_and_map_citations(
    answer: str, chunks: list[RetrievedChunk]
) -> list[Citation]:
    if not isinstance(answer, str) or not answer.strip():
        raise CitationValidationError("answer is empty")
    sentences = _claim_sentences(answer)
    for sentence in sentences:
        _validate_sentence(sentence, chunks)

    marker_order: OrderedDict[int, None] = OrderedDict()
    for marker in extract_markers(answer):
        if marker < 1 or marker > len(chunks):
            raise CitationValidationError(f"citation marker is out of range: {marker}")
        marker_order.setdefault(marker, None)

    if not marker_order and REFUSAL_TEXT.rstrip("。") not in answer:
        raise CitationValidationError("answer contains no citations")

    citations: list[Citation] = []
    for marker in marker_order:
        chunk = chunks[marker - 1]
        quote = _quote_for_marker(marker, sentences, chunk)
        if quote not in chunk["text"]:
            raise CitationValidationError("mapped quote is not an exact source substring")
        citations.append(
            {
                "marker": marker,
                "chunk_id": chunk["chunk_id"],
                "doc_title": chunk["doc_title"],
                "article": chunk["article"],
                "quote": quote,
                "page_url": chunk["page_url"],
                "file_url": chunk["file_url"],
            }
        )
    return citations


def citation_coverage(answer: str) -> float:
    factual = [sentence for sentence in _claim_sentences(answer) if not _is_exempt_sentence(sentence)]
    if not factual:
        return 1.0
    covered = sum(bool(extract_markers(sentence)) for sentence in factual)
    return covered / len(factual)

