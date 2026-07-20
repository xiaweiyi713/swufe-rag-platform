"""Advanced retrieval orchestration preserving the frozen output contract."""

from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
from pathlib import Path
from threading import Event, RLock
from typing import Any
import os
import re
import time

import numpy as np

from contracts import RetrievedChunk
from retrieval.embed import BGEEncoder, Encoder
from retrieval.query import analyze_query, chunk_search_text, entity_coverage, exact_signal_score, lexical_tokens, normalize_query
from retrieval.reranker import BGEReranker, HeuristicReranker, Reranker, rerank_documents
from retrieval.retriever import HybridRetriever


@dataclass(frozen=True)
class RetrievalTuning:
    candidate_k: int = 20
    dense_weight: float = 0.35
    lexical_weight: float = 0.25
    rerank_weight: float = 0.35
    rank_prior_weight: float = 0.05
    mmr_lambda: float = 0.88

    def __post_init__(self) -> None:
        weights = (
            self.dense_weight,
            self.lexical_weight,
            self.rerank_weight,
            self.rank_prior_weight,
        )
        if self.candidate_k < 5:
            raise ValueError("candidate_k must be at least 5")
        if any(weight < 0 for weight in weights) or not np.isclose(sum(weights), 1.0):
            raise ValueError("retrieval weights must be non-negative and sum to 1")
        if not 0 <= self.mmr_lambda <= 1:
            raise ValueError("mmr_lambda must be between 0 and 1")


class AdvancedRetriever:
    """Query expansion, exact-signal weighting, reranking, dedupe and MMR."""

    def __init__(
        self,
        core: HybridRetriever,
        *,
        reranker: Reranker | None = None,
        tuning: RetrievalTuning | None = None,
    ) -> None:
        self.core = core
        self.reranker = reranker
        self.tuning = tuning or RetrievalTuning(candidate_k=core.candidate_k)
        self._embedding_by_id = {
            chunk["chunk_id"]: core.bundle.embeddings[index]
            for index, chunk in enumerate(core.bundle.chunks)
        }
        self._retrieval_cache_lock = RLock()
        self._retrieval_cache: OrderedDict[
            tuple[Any, ...], tuple[float, list[RetrievedChunk]]
        ] = OrderedDict()
        self._retrieval_inflight: dict[tuple[Any, ...], Event] = {}
        self._retrieval_cache_size = self._positive_env_int(
            "SWUFE_RAG_RETRIEVAL_CACHE_SIZE", 512
        )
        self._retrieval_cache_ttl = self._positive_env_float(
            "SWUFE_RAG_RETRIEVAL_CACHE_TTL", 300.0
        )

    @staticmethod
    def _positive_env_int(name: str, default: int) -> int:
        try:
            value = int((os.getenv(name) or "").strip())
        except ValueError:
            return default
        return value if value > 0 else default

    @staticmethod
    def _positive_env_float(name: str, default: float) -> float:
        try:
            value = float((os.getenv(name) or "").strip())
        except ValueError:
            return default
        return value if value > 0 else default

    @staticmethod
    def _copy_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        # Runtime stages only read retrieved chunks.  Returning fresh dicts
        # keeps cached evidence isolated from payload enrichment.
        return [dict(chunk) for chunk in chunks]

    def _cached_retrieval(self, key: tuple[Any, ...]) -> list[RetrievedChunk] | None:
        now = time.monotonic()
        with self._retrieval_cache_lock:
            item = self._retrieval_cache.get(key)
            if item is None:
                return None
            expires_at, chunks = item
            if expires_at <= now:
                self._retrieval_cache.pop(key, None)
                return None
            self._retrieval_cache.move_to_end(key)
            return self._copy_chunks(chunks)

    def _store_retrieval(
        self, key: tuple[Any, ...], chunks: list[RetrievedChunk]
    ) -> None:
        with self._retrieval_cache_lock:
            self._retrieval_cache[key] = (
                time.monotonic() + self._retrieval_cache_ttl,
                self._copy_chunks(chunks),
            )
            self._retrieval_cache.move_to_end(key)
            while len(self._retrieval_cache) > self._retrieval_cache_size:
                self._retrieval_cache.popitem(last=False)

    @classmethod
    def from_artifacts(
        cls,
        chunks_path: str | Path = "data/chunks.jsonl",
        artifacts_dir: str | Path = "artifacts",
        encoder: Encoder | None = None,
        *,
        use_reranker: bool = True,
        rerank_model: str = "BAAI/bge-reranker-base",
        use_bm25: bool = True,
        rrf_k: int = 60,
        tuning: RetrievalTuning | None = None,
        sources_path: str | Path = "data/sources.csv",
        metadata_path: str | Path = "data/metadata.sqlite3",
    ) -> "AdvancedRetriever":
        actual_encoder = encoder or BGEEncoder()
        actual_tuning = tuning or RetrievalTuning()
        core = HybridRetriever.from_artifacts(
            chunks_path,
            artifacts_dir,
            actual_encoder,
            candidate_k=actual_tuning.candidate_k,
            rrf_k=rrf_k,
            use_bm25=use_bm25,
            sources_path=sources_path,
            metadata_path=metadata_path,
        )
        reranker = BGEReranker(rerank_model) if use_reranker else None
        return cls(core, reranker=reranker, tuning=actual_tuning)

    @staticmethod
    def _deduplicate(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        seen_ids: set[str] = set()
        seen_text: set[str] = set()
        unique: list[RetrievedChunk] = []
        for chunk in chunks:
            text = str(chunk["text"])
            body = text.split("\n", 1)[-1] if "\n" in text else text
            body = re.sub(r"原文件第\d+页|第\d+页表格", "", body)
            fingerprint = "".join(
                character.lower()
                for character in body
                if character.isalnum()
            )
            if chunk["chunk_id"] in seen_ids or fingerprint in seen_text:
                continue
            seen_ids.add(chunk["chunk_id"])
            seen_text.add(fingerprint)
            unique.append(chunk)
        return unique

    def _lexical_score(self, analysis, chunk: RetrievedChunk) -> float:
        document_tokens = lexical_tokens(chunk_search_text(chunk))
        coverage = (
            len(analysis.tokens & document_tokens) / len(analysis.tokens)
            if analysis.tokens
            else 0.0
        )
        return min(1.0, coverage * 0.65 + exact_signal_score(analysis, chunk) * 0.35)

    def _select_mmr(
        self,
        chunks: list[RetrievedChunk],
        relevance: np.ndarray,
        top_k: int,
    ) -> list[int]:
        if not chunks:
            return []
        remaining = set(range(len(chunks)))
        selected: list[int] = []
        while remaining and len(selected) < top_k:
            best_index = None
            best_key = None
            for index in sorted(remaining):
                diversity = 0.0
                vector = self._embedding_by_id.get(chunks[index]["chunk_id"])
                if selected and vector is not None:
                    similarities = []
                    for chosen in selected:
                        chosen_vector = self._embedding_by_id.get(chunks[chosen]["chunk_id"])
                        if chosen_vector is not None:
                            similarities.append(float(vector @ chosen_vector))
                    diversity = max(similarities, default=0.0)
                mmr = self.tuning.mmr_lambda * float(relevance[index]) - (
                    1 - self.tuning.mmr_lambda
                ) * max(diversity, 0.0)
                key = (mmr, float(relevance[index]), -index)
                if best_key is None or key > best_key:
                    best_key = key
                    best_index = index
            assert best_index is not None
            selected.append(best_index)
            remaining.remove(best_index)
        return selected

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        college: str | None = None,
        cohort: str | None = None,
    ) -> list[RetrievedChunk]:
        return self.retrieve_scoped(
            query,
            top_k=top_k,
            college=college,
            cohort=cohort,
        )

    def _retrieve_scoped_uncached(
        self,
        query: str,
        top_k: int = 5,
        college: str | None = None,
        cohort: str | None = None,
        *,
        policy_year: int | None = None,
        topic: str | None = None,
    ) -> list[RetrievedChunk]:
        analysis = analyze_query(query)
        window = min(50, max(self.tuning.candidate_k, top_k * 4))
        candidates = self._deduplicate(
            self.core.retrieve_scoped(
                analysis.expanded,
                window,
                college,
                cohort,
                policy_year=policy_year,
                topic=topic,
            )
        )
        if not candidates:
            return []
        # When an official program-specific extract exists, it is the
        # authoritative retrieval record for that major/cohort. The complete
        # book remains indexed for coverage, but its duplicate passages must
        # not occupy the same Top-K window or carry stale program scope.
        cohort_match = re.search(r"(?<!\d)((?:19|20)\d{2})级", analysis.normalized)
        major_markers = [
            marker
            for marker in ("计算机科学与技术专业", "人工智能专业")
            if marker in analysis.normalized
        ]
        if cohort_match and len(major_markers) == 1:
            marker = major_markers[0]
            cohort_marker = cohort_match.group(1) + "级"
            specific = [
                chunk
                for chunk in candidates
                if marker in chunk["doc_title"]
                and cohort_marker in chunk["doc_title"]
                and "完整总册" not in chunk["doc_title"]
            ]
            if specific:
                candidates = specific
        # A school-wide question about the recommended graduation-credit range
        # asks for the curriculum principle, not the minimum of an arbitrary
        # individual major. Keep the exact principle clauses when present so
        # unrelated major totals do not create an artificial conflict for the
        # fail-closed answer generator.
        if re.search(r"建议.*毕业.*学分.*范围|毕业.*学分.*范围", analysis.normalized):
            principle = [
                chunk
                for chunk in candidates
                if "各专业人才培养方案建议毕业学分要求"
                in normalize_query(chunk_search_text(chunk))
            ]
            if principle:
                candidates = principle
        # Explicit course codes and named Latin entities are evidence-bearing.
        # If the candidate window contains exact supporting chunks, prevent a
        # semantic reranker from pushing all of them out of the returned set.
        entity_matched = [
            chunk for chunk in candidates if entity_coverage(analysis, [chunk])
        ]
        if analysis.required_entities and entity_matched:
            candidates = entity_matched

        dense = np.asarray(
            [max(0.0, min(1.0, (chunk["score"] + 1.0) / 2.0)) for chunk in candidates],
            dtype=np.float32,
        )
        lexical = np.asarray(
            [self._lexical_score(analysis, chunk) for chunk in candidates],
            dtype=np.float32,
        )
        prior = np.asarray(
            [1.0 / (rank + 1) for rank in range(len(candidates))], dtype=np.float32
        )
        active_reranker = self.reranker or HeuristicReranker(analysis, candidates)
        reranked = rerank_documents(active_reranker, analysis.normalized, candidates)
        relevance = (
            self.tuning.dense_weight * dense
            + self.tuning.lexical_weight * lexical
            + self.tuning.rerank_weight * reranked
            + self.tuning.rank_prior_weight * prior
        )
        # A selected college is a strong user constraint. School-level chunks
        # remain eligible for university-wide rules, but they must not crowd a
        # matching college plan out of a small result window merely because the
        # full curriculum book contains many near-identical passages.
        if college:
            relevance += np.asarray(
                [0.12 if chunk["college"] == college else 0.0 for chunk in candidates],
                dtype=np.float32,
            )
        stable_order = sorted(
            range(len(candidates)),
            key=lambda index: (
                -float(relevance[index]),
                -float(reranked[index]),
                candidates[index]["chunk_id"],
            ),
        )
        ordered_chunks = [candidates[index] for index in stable_order]
        ordered_scores = relevance[stable_order]
        selected = self._select_mmr(
            ordered_chunks, ordered_scores, min(top_k, len(ordered_chunks))
        )
        results = [ordered_chunks[index] for index in selected]

        # Keep at least one item from the explicitly selected college whenever
        # such evidence exists. This prevents dozens of similar school-level
        # plan passages from occupying the complete Top-K window.
        if college and results and not any(
            chunk["college"] == college for chunk in results
        ):
            college_evidence = next(
                (chunk for chunk in ordered_chunks if chunk["college"] == college),
                None,
            )
            if college_evidence is not None:
                results[-1] = college_evidence

        # Comparison answers need evidence from every explicitly named major.
        # Dense/MMR ranking can otherwise fill the window with near-duplicate
        # tables from one plan only.
        major_markers = [
            marker
            for marker in ("计算机科学与技术专业", "人工智能专业")
            if marker in analysis.normalized
        ]
        if len(major_markers) > 1 and results:
            for marker in major_markers:
                if any(marker in chunk_search_text(chunk) for chunk in results):
                    continue
                replacement = next(
                    (
                        chunk
                        for chunk in ordered_chunks
                        if marker in chunk_search_text(chunk)
                        and all(chunk["chunk_id"] != item["chunk_id"] for item in results)
                    ),
                    None,
                )
                if replacement is not None:
                    results[-1] = replacement
        return results

    def retrieve_scoped(
        self,
        query: str,
        top_k: int = 5,
        college: str | None = None,
        cohort: str | None = None,
        *,
        policy_year: int | None = None,
        topic: str | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve with a bounded TTL cache and same-key singleflight.

        The cache is deliberately local to a loaded runtime: embeddings and
        metadata are immutable for that runtime, while Redis remains the
        cross-process answer-cache layer.  Singleflight prevents identical
        misses from concurrently invoking MPS/CPU model inference.
        """
        key = (
            query.strip() if isinstance(query, str) else query,
            int(top_k),
            college.strip() if isinstance(college, str) else college,
            cohort.strip() if isinstance(cohort, str) else cohort,
            policy_year,
            topic.strip() if isinstance(topic, str) else topic,
        )
        cached = self._cached_retrieval(key)
        if cached is not None:
            return cached

        with self._retrieval_cache_lock:
            event = self._retrieval_inflight.get(key)
            owner = event is None
            if owner:
                event = Event()
                self._retrieval_inflight[key] = event
        assert event is not None
        if not owner:
            event.wait(timeout=max(30.0, self._retrieval_cache_ttl))
            cached = self._cached_retrieval(key)
            if cached is not None:
                return cached
            # The owner failed or timed out.  Falling through keeps the request
            # functional instead of turning a cache coordination issue into a
            # user-visible error.

        try:
            result = self._retrieve_scoped_uncached(
                query,
                top_k=top_k,
                college=college,
                cohort=cohort,
                policy_year=policy_year,
                topic=topic,
            )
            self._store_retrieval(key, result)
            return self._copy_chunks(result)
        finally:
            if owner:
                with self._retrieval_cache_lock:
                    self._retrieval_inflight.pop(key, None)
                    event.set()


_default_retriever: AdvancedRetriever | None = None
_default_lock = RLock()


def configure_default(retriever: AdvancedRetriever | None) -> None:
    global _default_retriever
    with _default_lock:
        _default_retriever = retriever


def _get_default() -> AdvancedRetriever:
    global _default_retriever
    with _default_lock:
        if _default_retriever is None:
            _default_retriever = AdvancedRetriever.from_artifacts()
        return _default_retriever


def retrieve(
    query: str,
    top_k: int = 5,
    college: str | None = None,
    cohort: str | None = None,
) -> list[RetrievedChunk]:
    return _get_default().retrieve(query, top_k, college, cohort)
