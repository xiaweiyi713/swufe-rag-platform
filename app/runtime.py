"""Replaceable runtime adapter shared by the debug API and future frontends."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from contracts import AnswerResult, CHUNK_FIELDS, KnowledgeChunk, RetrievedChunk
from generation.context import ContextBuilder
from generation.pipeline import AdvancedGenerationService, service_from_config as generation_service_from_config
from generation.general_chat import GeneralChatService, service_from_config as general_service_from_config
from generation.llm import OpenAICompatibleClient
from retrieval.embed import BGEEncoder, HashingEncoder
from retrieval.index import IndexBundle, file_sha256, load_chunks
from retrieval.pipeline import AdvancedRetriever, RetrievalTuning
from retrieval.retriever import HybridRetriever
from app.demo_llm import DemoGeneralClient, DemoGroundedClient
from storage.metadata_db import MetadataDB
from swufe_rag.orchestration import HybridRuntime
from swufe_rag.routing.router import HybridRouter, LLMRouteClassifier


DEMO_CHUNKS = Path(__file__).parents[1] / "tests" / "fixtures" / "chunks.jsonl"

RetrieveFunction = Callable[..., list[RetrievedChunk]]
AnswerFunction = Callable[[str, list[dict[str, Any]]], AnswerResult]


def _load_config(path: str | Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required; install requirements.txt") from exc
    with Path(path).open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError("runtime config must be a mapping")
    return value


def _runtime_fingerprint(
    retriever: AdvancedRetriever,
    *,
    chunks_path: str | Path,
    sources_path: str | Path,
    metadata_path: str | Path,
    config_path: str | Path,
    artifacts_path: str | Path,
) -> dict[str, Any]:
    """Expose the exact files and loaded index identity used by the web process."""

    chunks = Path(chunks_path).resolve()
    sources = Path(sources_path).resolve()
    metadata = Path(metadata_path).resolve()
    config = Path(config_path).resolve()
    artifacts = Path(artifacts_path).resolve()
    manifest_path = artifacts / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    encoder = retriever.core.encoder
    model = getattr(encoder, "_model", None)
    actual_device = str(getattr(model, "device", getattr(encoder, "device", None)))
    cuda_available = False
    cuda_device = None
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            cuda_device = torch.cuda.get_device_name(0)
    except ImportError:
        pass
    faiss_index = retriever.core.bundle.faiss_index
    return {
        "pid": os.getpid(),
        "python_executable": sys.executable,
        "working_directory": str(Path.cwd().resolve()),
        "chunks_path": str(chunks),
        "chunks_sha256": file_sha256(chunks),
        "sources_path": str(sources),
        "sources_sha256": file_sha256(sources),
        "metadata_path": str(metadata),
        "config_path": str(config),
        "config_sha256": file_sha256(config),
        "artifacts_path": str(artifacts),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": (
            file_sha256(manifest_path) if manifest_path.is_file() else None
        ),
        "manifest_chunks_sha256": manifest.get("chunks_sha256"),
        "index_backend": retriever.core.bundle.backend,
        "index_model": retriever.core.bundle.model_name,
        "index_dimension": int(retriever.core.bundle.embeddings.shape[1]),
        "index_rows": int(retriever.core.bundle.embeddings.shape[0]),
        "faiss_rows": int(faiss_index.ntotal) if faiss_index is not None else None,
        "encoder_device": actual_device,
        "cuda_available": cuda_available,
        "cuda_device": cuda_device,
    }


def _build_production_pipelines(
    chunks_path: str | Path,
    *,
    sources_path: str | Path,
    metadata_path: str | Path,
    config_path: str | Path,
) -> tuple[AdvancedRetriever, AdvancedGenerationService, dict[str, Any]]:
    config = _load_config(config_path)
    paths = config.get("paths", {})
    retrieval = config.get("retrieval", {})
    if not isinstance(paths, dict) or not isinstance(retrieval, dict):
        raise ValueError("paths and retrieval config sections must be mappings")
    encoder = BGEEncoder(
        str(retrieval.get("embed_model", "BAAI/bge-large-zh-v1.5")),
        query_prefix=str(
            retrieval.get(
                "query_prefix",
                "\u4e3a\u8fd9\u4e2a\u53e5\u5b50\u751f\u6210\u8868\u793a\u4ee5\u7528\u4e8e\u68c0\u7d22\u76f8\u5173\u6587\u7ae0\uff1a",
            )
        ),
    )
    tuning = RetrievalTuning(
        candidate_k=int(retrieval.get("candidate_k", 20)),
        dense_weight=float(retrieval.get("dense_weight", 0.35)),
        lexical_weight=float(retrieval.get("lexical_weight", 0.25)),
        rerank_weight=float(retrieval.get("rerank_weight", 0.35)),
        rank_prior_weight=float(retrieval.get("rank_prior_weight", 0.05)),
        mmr_lambda=float(retrieval.get("mmr_lambda", 0.88)),
    )
    retriever = AdvancedRetriever.from_artifacts(
        chunks_path,
        str(paths.get("artifacts", "artifacts")),
        encoder,
        use_reranker=bool(retrieval.get("use_reranker", True)),
        rerank_model=str(retrieval.get("rerank_model", "BAAI/bge-reranker-base")),
        use_bm25=bool(retrieval.get("use_bm25", True)),
        rrf_k=int(retrieval.get("rrf_k", 60)),
        tuning=tuning,
        sources_path=sources_path,
        metadata_path=metadata_path,
    )
    return retriever, generation_service_from_config(config_path), config


@dataclass
class RAGRuntime:
    retrieve_function: RetrieveFunction
    answer_function: AnswerFunction
    chunks: list[KnowledgeChunk]
    mode: str
    _chunks_by_id: dict[str, KnowledgeChunk] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._chunks_by_id = {chunk["chunk_id"]: chunk for chunk in self.chunks}

    def retrieve(
        self,
        question: str,
        *,
        top_k: int = 5,
        college: str | None = None,
        cohort: str | None = None,
        policy_year: int | None = None,
        topic: str | None = None,
    ) -> list[RetrievedChunk]:
        if policy_year is None and topic is None:
            return self.retrieve_function(question, top_k, college, cohort)
        return self.retrieve_function(
            question,
            top_k,
            college,
            cohort,
            policy_year=policy_year,
            topic=topic,
        )

    def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        college: str | None = None,
        cohort: str | None = None,
        policy_year: int | None = None,
        topic: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        retrieved = self.retrieve(
            question,
            top_k=top_k,
            college=college,
            cohort=cohort,
            policy_year=policy_year,
            topic=topic,
        )
        result = self.answer_function(question, retrieved)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            **result,
            "retrieved": [
                {
                    "chunk_id": chunk["chunk_id"],
                    "doc_title": chunk["doc_title"],
                    "article": chunk["article"],
                    "college": chunk["college"],
                    "cohort": chunk["cohort"],
                    "score": chunk["score"],
                    "is_table": chunk["is_table"],
                    "summary": chunk["text"][:260],
                }
                for chunk in retrieved
            ],
            "latency_ms": latency_ms,
        }

    def debug_ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        college: str | None = None,
        cohort: str | None = None,
    ) -> dict[str, Any]:
        """Return the formal D payload plus debug-only runtime metadata."""

        return {
            **self.ask(
                question,
                top_k=top_k,
                college=college,
                cohort=cohort,
            ),
            "mode": self.mode,
        }

    def source(self, chunk_id: str) -> KnowledgeChunk | None:
        chunk = self._chunks_by_id.get(chunk_id)
        if chunk is None:
            return None
        return {key: chunk[key] for key in CHUNK_FIELDS}  # type: ignore[return-value]

    def options(self) -> dict[str, Any]:
        colleges = sorted(
            {chunk["college"] for chunk in self.chunks if chunk["level"] == "院级"}
        )
        cohorts = sorted(
            {chunk["cohort"] for chunk in self.chunks if chunk["cohort"] != "不限"}
        )
        return {
            "mode": self.mode,
            "colleges": colleges,
            "cohorts": cohorts,
            "chunk_count": len(self.chunks),
            "default_top_k": 5,
        }


def build_demo_runtime(path: str | Path = DEMO_CHUNKS) -> RAGRuntime:
    chunks_path = Path(path)
    chunks = load_chunks(chunks_path)
    encoder = HashingEncoder(128)
    embeddings = encoder.encode_documents([chunk["text"] for chunk in chunks])
    bundle = IndexBundle(
        chunks=chunks,
        embeddings=embeddings,
        model_name=encoder.model_name,
        source_hash=file_sha256(chunks_path),
        backend="demo-memory",
    )
    retriever = AdvancedRetriever(HybridRetriever(bundle, encoder))
    generation = AdvancedGenerationService(DemoGroundedClient(), refuse_th=0.30)
    return RAGRuntime(retriever.retrieve_scoped, generation.answer, chunks, "demo")


def build_review_runtime(path: str | Path = "data/chunks.jsonl") -> RAGRuntime:
    """Build an offline review runtime over an explicitly supplied real chunk file.

    This is a data/UX inspection aid, not a production-quality embedding or LLM
    evaluation.  It never substitutes fixtures and it does not alter the frozen
    production ``retrieve``/``answer`` facade.
    """

    chunks_path = Path(path)
    chunks = load_chunks(chunks_path)
    encoder = HashingEncoder(512)
    embeddings = encoder.encode_documents([chunk["text"] for chunk in chunks])
    bundle = IndexBundle(
        chunks=chunks,
        embeddings=embeddings,
        model_name=encoder.model_name,
        source_hash=file_sha256(chunks_path),
        backend="review-memory",
    )
    tuning = RetrievalTuning(candidate_k=50)
    retriever = AdvancedRetriever(
        HybridRetriever(bundle, encoder, candidate_k=50), tuning=tuning
    )
    generation = AdvancedGenerationService(DemoGroundedClient(), refuse_th=0.34)
    return RAGRuntime(retriever.retrieve_scoped, generation.answer, chunks, "review")


def build_production_runtime(
    chunks_path: str | Path = "data/chunks.jsonl",
    *,
    sources_path: str | Path = "data/sources.csv",
    metadata_path: str | Path = "data/metadata.sqlite3",
    config_path: str | Path = "config.advanced.yaml",
) -> RAGRuntime:
    """Build D's adapter with explicit, path-bound B/C pipelines.

    This function never falls back to fixtures. Missing production chunks or
    retrieval artifacts therefore remain visible at the HTTP boundary.
    """

    chunks = load_chunks(chunks_path)
    retriever, generation, _ = _build_production_pipelines(
        chunks_path,
        sources_path=sources_path,
        metadata_path=metadata_path,
        config_path=config_path,
    )
    return RAGRuntime(retriever.retrieve_scoped, generation.answer, chunks, "production")


def build_local_runtime(
    chunks_path: str | Path = "data/chunks.jsonl",
    *,
    sources_path: str | Path = "data/sources.csv",
    metadata_path: str | Path = "data/metadata.sqlite3",
    config_path: str | Path = "config.advanced.yaml",
) -> RAGRuntime:
    """Use production BGE/FAISS with deterministic grounded debug generation.

    This mode is intentionally local-only: it exercises the real A/B data and
    the complete C evidence/citation validators without requiring an external
    LLM credential. It is never used by the formal product server.
    """

    chunks = load_chunks(chunks_path)
    retriever, _, config = _build_production_pipelines(
        chunks_path,
        sources_path=sources_path,
        metadata_path=metadata_path,
        config_path=config_path,
    )
    generation_config = config.get("generation", {})
    generation = AdvancedGenerationService(
        DemoGroundedClient(),
        refuse_th=float(generation_config.get("refuse_th", 0.35)),
    )
    return RAGRuntime(
        retriever.retrieve_scoped, generation.answer, chunks, "local-bge-faiss"
    )


def _demo_hybrid(
    path: str | Path,
    *,
    dimension: int,
    mode: str,
    refuse_th: float,
) -> HybridRuntime:
    chunks_path = Path(path)
    chunks = load_chunks(chunks_path)
    encoder = HashingEncoder(dimension)
    embeddings = encoder.encode_documents([chunk["text"] for chunk in chunks])
    bundle = IndexBundle(
        chunks=chunks,
        embeddings=embeddings,
        model_name=encoder.model_name,
        source_hash=file_sha256(chunks_path),
        backend=f"{mode}-memory",
    )
    metadata_db = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    retriever = AdvancedRetriever(
        HybridRetriever(bundle, encoder, metadata_db=metadata_db)
    )
    generation = AdvancedGenerationService(
        DemoGroundedClient(), refuse_th=refuse_th
    )
    router = HybridRouter(known_colleges=metadata_db.known_colleges())
    return HybridRuntime(
        router=router,
        school_retrieve=retriever.retrieve_scoped,
        school_answer=generation.answer,
        general_chat=GeneralChatService(DemoGeneralClient()),
        metadata_db=metadata_db,
        runtime_mode=f"{mode}-hybrid",
    )


def build_demo_hybrid_runtime(
    path: str | Path = DEMO_CHUNKS,
) -> HybridRuntime:
    return _demo_hybrid(path, dimension=128, mode="demo", refuse_th=0.35)


def build_review_hybrid_runtime(
    path: str | Path = "data/chunks.jsonl",
) -> HybridRuntime:
    return _demo_hybrid(path, dimension=512, mode="review", refuse_th=0.34)


def build_local_hybrid_runtime(
    chunks_path: str | Path = "data/chunks.jsonl",
    *,
    sources_path: str | Path = "data/sources.csv",
    metadata_path: str | Path = "data/metadata.sqlite3",
    config_path: str | Path = "config.advanced.yaml",
) -> HybridRuntime:
    """Run the complete product route with formal BGE/FAISS and no cloud key.

    Retrieval, metadata scoping, citation binding and refusal gates are the
    production implementations. Only route/general/grounded text generation
    use deterministic local clients so the integration can be tested offline.
    """

    retriever, _, config = _build_production_pipelines(
        chunks_path,
        sources_path=sources_path,
        metadata_path=metadata_path,
        config_path=config_path,
    )
    generation_config = config.get("generation", {})
    generation = AdvancedGenerationService(
        DemoGroundedClient(),
        refuse_th=float(generation_config.get("refuse_th", 0.35)),
    )
    metadata_db = MetadataDB.from_files(
        sources_path=sources_path,
        chunks_path=chunks_path,
        database=metadata_path,
    )
    runtime_info = _runtime_fingerprint(
        retriever,
        chunks_path=chunks_path,
        sources_path=sources_path,
        metadata_path=metadata_path,
        config_path=config_path,
        artifacts_path=config.get("paths", {}).get("artifacts", "artifacts"),
    )
    runtime = HybridRuntime(
        router=HybridRouter(known_colleges=metadata_db.known_colleges()),
        school_retrieve=retriever.retrieve_scoped,
        school_answer=generation.answer,
        general_chat=GeneralChatService(DemoGeneralClient()),
        metadata_db=metadata_db,
        runtime_mode="local-bge-faiss-hybrid",
        runtime_info=runtime_info,
    )
    runtime.runtime_info = runtime_info
    return runtime


def build_request_llm_runtime(
    base_runtime: HybridRuntime,
    api_key: str,
    *,
    config_path: str | Path = "config.advanced.yaml",
) -> HybridRuntime:
    """Create a request-scoped real-LLM runtime without persisting its key.

    The expensive retriever, trusted metadata store and non-secret session
    history are shared with the already-loaded runtime. Provider clients and
    the API key live only on the returned object, which the HTTP endpoint does
    not cache after the request completes.
    """

    if not isinstance(base_runtime, HybridRuntime):
        raise ValueError("request API keys require a HybridRuntime")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("X-LLM-API-Key must not be blank")

    config = _load_config(config_path)
    generation = config.get("generation", {})
    if not isinstance(generation, dict):
        raise ValueError("generation config must be a mapping")
    clean_key = api_key.strip()
    model_spec = str(generation.get("llm", "deepseek-chat"))
    max_retries = int(generation.get("max_retries", 2))
    timeout_seconds = float(generation.get("request_timeout_seconds", 60))

    grounded_client = OpenAICompatibleClient(
        model_spec,
        api_key=clean_key,
        temperature=float(generation.get("temperature", 0)),
        max_retries=max_retries,
        timeout_seconds=timeout_seconds,
    )
    route_client = OpenAICompatibleClient(
        model_spec,
        api_key=clean_key,
        temperature=0,
        max_retries=max_retries,
        timeout_seconds=timeout_seconds,
    )
    general_client = OpenAICompatibleClient(
        model_spec,
        api_key=clean_key,
        temperature=float(generation.get("general_temperature", 0.7)),
        max_retries=max_retries,
        timeout_seconds=timeout_seconds,
    )
    grounded = AdvancedGenerationService(
        grounded_client,
        refuse_th=float(generation.get("refuse_th", 0.35)),
        context_builder=ContextBuilder(
            max_context_chars=int(generation.get("max_context_chars", 7000)),
            max_chunk_chars=int(generation.get("max_chunk_chars", 1600)),
        ),
    )
    known_colleges = base_runtime.metadata_db.known_colleges()
    runtime = HybridRuntime(
        router=HybridRouter(
            LLMRouteClassifier(route_client),
            known_colleges=known_colleges,
        ),
        school_retrieve=base_runtime.school_retrieve,
        school_answer=grounded.answer,
        general_chat=GeneralChatService(general_client),
        metadata_db=base_runtime.metadata_db,
        sessions=base_runtime.sessions,
        runtime_mode=f"{base_runtime.mode}+request-llm",
        runtime_info=getattr(base_runtime, "runtime_info", {}),
    )
    runtime.runtime_info = getattr(base_runtime, "runtime_info", {})
    return runtime


def build_production_hybrid_runtime(
    chunks_path: str | Path = "data/chunks.jsonl",
    *,
    sources_path: str | Path = "data/sources.csv",
    metadata_path: str | Path = "data/metadata.sqlite3",
    config_path: str | Path = "config.advanced.yaml",
) -> HybridRuntime:
    """Build the route-first product runtime without any fixture fallback."""

    retriever, generation_service, config = _build_production_pipelines(
        chunks_path,
        sources_path=sources_path,
        metadata_path=metadata_path,
        config_path=config_path,
    )
    generation = config.get("generation", {})
    route_client = OpenAICompatibleClient(
        str(generation.get("llm", "deepseek-chat")),
        temperature=0,
        max_retries=int(generation.get("max_retries", 2)),
        timeout_seconds=float(generation.get("request_timeout_seconds", 60)),
    )
    metadata_db = MetadataDB.from_files(
        sources_path=sources_path,
        chunks_path=chunks_path,
        database=metadata_path,
    )
    router = HybridRouter(
        LLMRouteClassifier(route_client),
        known_colleges=metadata_db.known_colleges(),
    )
    runtime_info = _runtime_fingerprint(
        retriever,
        chunks_path=chunks_path,
        sources_path=sources_path,
        metadata_path=metadata_path,
        config_path=config_path,
        artifacts_path=config.get("paths", {}).get("artifacts", "artifacts"),
    )
    runtime = HybridRuntime(
        router=router,
        school_retrieve=retriever.retrieve_scoped,
        school_answer=generation_service.answer,
        general_chat=general_service_from_config(config_path),
        metadata_db=metadata_db,
        runtime_mode="production-hybrid",
        runtime_info=runtime_info,
    )
    runtime.runtime_info = runtime_info
    return runtime
