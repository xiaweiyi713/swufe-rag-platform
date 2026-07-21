"""Formal HTTP application for the typed V16 query pipeline."""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from threading import Lock, RLock, Thread
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from academic_audit import CurriculumAuditService
from app.llm_url_policy import validate_request_llm_base_url
from app.runtime_factory import build_local_query_runtime, build_request_query_runtime
from app.server.capacity import QueryCapacityError, QueryCapacityLimiter
from app.server.health import readiness_report, redis_status
from app.server.ratelimit import EXEMPT_PATHS, RateLimiter, client_identity
from app.server.web_search import format_web_context, search_web
from contracts import ContractError, GenerationUnavailableError, KnowledgeBaseNotReadyError
from generation.llm import OpenAICompatibleClient
from swufe_rag.redis_support import (
    RedisUnavailableError,
    SessionLockTimeoutError,
    build_answer_cache,
    build_session_store,
    cacheable_answer,
    component_info,
    runtime_cache_namespace,
    redis_required,
    session_guard,
    session_has_history,
)


STATIC_DIR = Path(__file__).parent.parent / "static"


def _stream_preview_text(answer_md: str) -> str:
    """Return display-safe incremental text for a validated school answer.

    Source titles and physical page labels remain visible while the answer is
    revealed. Interactive links stay exclusive to the final event so clients
    never render half-written Markdown or URLs.
    """

    lines: list[str] = []
    for raw_line in answer_md.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        lines.append(stripped if stripped else "")

    preview = "\n".join(lines)
    preview = re.sub(
        r"\s*·\s*\[下载原文件\]\([^\r\n)]*\)",
        "",
        preview,
    )
    preview = re.sub(
        r"\[下载原文件\]\([^\r\n)]*\)",
        "",
        preview,
    )
    preview = re.sub(r"\[([^\]]+)\]\([^\s)]+(?:#[^\s)]*)?\)", r"\1", preview)
    preview = re.sub(r"\*\*([^*]+)\*\*", r"\1", preview)
    preview = re.sub(r"__([^_]+)__", r"\1", preview)
    preview = re.sub(r"\n{3,}", "\n\n", preview)
    return preview.strip()


def _school_web_search_query(question: str) -> str:
    clean = question.strip()
    if re.search(r"西南财经大学|西财", clean):
        return clean
    return f"西南财经大学 {clean}"


SECURITY_WEB_FALLBACK_RE = re.compile(
    r"忽略.{0,20}(?:规则|指令|提示)|"
    r"(?:编造|编一个|伪造).{0,20}(?:校规|规定|官网|链接)|"
    r"输出.{0,12}(?:系统提示词|知识库密钥|API\s*Key)",
    re.I,
)


def _needs_school_web_fallback(
    runtime: Any, payload: Any, question: str | None = None
) -> bool:
    capabilities = getattr(runtime, "capabilities", None)
    return bool(
        isinstance(payload, dict)
        and payload.get("mode") == "school_rag"
        and payload.get("refused")
        and not (
            isinstance(question, str)
            and SECURITY_WEB_FALLBACK_RE.search(question)
        )
        and getattr(capabilities, "general_llm", False)
        and callable(getattr(runtime, "attach_school_web_fallback", None))
    )


def _sanitize_no_proxy() -> None:
    for name in ("NO_PROXY", "no_proxy"):
        raw = os.getenv(name)
        if not raw:
            continue
        values = [value.strip() for value in raw.split(",") if value.strip()]
        compatible = [value for value in values if not value.startswith("::")]
        if compatible != values:
            os.environ[name] = ",".join(compatible)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AskRequest(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    college: str | None = None
    cohort: str | None = None
    major: str | None = None
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    deep_thinking: bool = False
    web_search: bool = False


class LLMModelsResponse(StrictModel):
    models: list[str]


class LLMValidationResponse(StrictModel):
    valid: bool
    model: str


class CompletedCourseRequest(StrictModel):
    code: str | None = None
    name: str | None = None
    credits: float | None = None


class AcademicAuditRequest(StrictModel):
    question: str | None = Field(default=None, min_length=1, max_length=2000)
    cohort: str | None = None
    major: str | None = None
    target_module: str | None = None
    current_semester: str | int | None = None
    completed_courses: list[str | CompletedCourseRequest] = Field(default_factory=list)


def create_app(
    runtime: Any | None = None,
    *,
    query_capacity: QueryCapacityLimiter | None = None,
    rate_limiter: RateLimiter | None = None,
    web_searcher: Any = search_web,
):
    try:
        from fastapi import FastAPI, Header, HTTPException, Request
        from fastapi.encoders import jsonable_encoder
        from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    except ImportError as exc:
        raise RuntimeError("web dependencies are missing") from exc

    state: dict[str, Any] = {
        "runtime": runtime,
        "audit": None,
        "answer_cache": None,
        "answer_cache_ready": False,
        "cache_namespace": None,
        "query_capacity": query_capacity or QueryCapacityLimiter.from_env(),
        "rate_limiter": rate_limiter or RateLimiter.from_env(),
        # 预热由启动钩子在后台线程完成;/ready 据此决定是否接流量。
        "warmup_error": None,
        "redis_probe": {"checked_at": 0.0, "status": None},
    }
    lock = RLock()
    runtime_build_lock = Lock()

    def get_answer_cache():
        with lock:
            if not state["answer_cache_ready"]:
                state["answer_cache"] = build_answer_cache()
                state["answer_cache_ready"] = True
            return state["answer_cache"]

    def get_runtime():
        with lock:
            existing = state["runtime"]
        if existing is not None:
            return existing

        # Model/index loading may take seconds. Keep it outside the state lock so
        # /readyz can continue returning a prompt 503 during warmup.
        with runtime_build_lock:
            with lock:
                existing = state["runtime"]
            if existing is not None:
                return existing
            _sanitize_no_proxy()
            if os.getenv("SWUFE_RAG_ALLOW_MODEL_DOWNLOAD") != "1":
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
                os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            local_runtime = build_local_query_runtime(
                os.getenv("SWUFE_RAG_CHUNKS", "data/chunks.jsonl"),
                sources_path=os.getenv("SWUFE_RAG_SOURCES", "data/sources.csv"),
                metadata_path=os.getenv("SWUFE_RAG_METADATA", "data/metadata.sqlite3"),
                config_path=os.getenv("SWUFE_RAG_CONFIG", "config.advanced.yaml"),
                academic_database=os.getenv(
                    "SWUFE_RAG_ACADEMIC_DB", "data/academic_v2.sqlite3"
                ),
            )
            local_runtime.sessions = build_session_store()
            server_key = (
                os.getenv("SWUFE_RAG_LLM_API_KEY")
                or os.getenv("OPENAI_API_KEY")
                or ""
            ).strip()
            if server_key:
                built = build_request_query_runtime(
                    local_runtime,
                    server_key,
                    config_path=os.getenv(
                        "SWUFE_RAG_CONFIG", "config.advanced.yaml"
                    ),
                    base_url=(
                        os.getenv("SWUFE_RAG_LLM_BASE_URL")
                        or os.getenv("OPENAI_BASE_URL")
                    ),
                    model_override=os.getenv("SWUFE_RAG_LLM_MODEL"),
                )
            else:
                built = local_runtime
            with lock:
                state["runtime"] = built
            return built

    def get_cache_namespace(runtime: Any) -> str:
        with lock:
            if state["cache_namespace"] is None:
                state["cache_namespace"] = runtime_cache_namespace(runtime)
            return state["cache_namespace"]

    def get_query_capacity() -> QueryCapacityLimiter:
        return state["query_capacity"]

    def required_redis_health() -> dict[str, Any]:
        """Bound Redis gating overhead while still failing closed quickly."""

        now = time.monotonic()
        with lock:
            cached = state["redis_probe"]
            if cached["status"] is not None and now - cached["checked_at"] < 1.0:
                return cached["status"]
        status = redis_status()
        with lock:
            state["redis_probe"] = {"checked_at": now, "status": status}
        return status

    def add_school_web_fallback(
        selected: Any,
        request: AskRequest,
        payload: dict[str, Any],
        existing_sources: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if not _needs_school_web_fallback(selected, payload, request.question):
            return payload
        started = time.perf_counter()
        try:
            sources = list(existing_sources or web_searcher(
                _school_web_search_query(request.question)
            ))
        except Exception:
            sources = []
        search_ms = round((time.perf_counter() - started) * 1000, 2)
        if not sources:
            return {
                **payload,
                "web_sources": [],
                "web_fallback": {
                    "attempted": True,
                    "used": False,
                    "reason": "no_web_results",
                    "source_count": 0,
                    "search_ms": search_ms,
                },
            }
        return selected.attach_school_web_fallback(
            request.question,
            payload,
            web_sources=sources,
            search_ms=search_ms,
        )

    def bind_retrieval_capacity(runtime: Any) -> bool:
        """Prefer runtime-owned retrieval admission over the legacy HTTP guard."""
        binder = getattr(runtime, "bind_retrieval_capacity", None)
        if not callable(binder):
            return False
        binder(get_query_capacity())
        return True

    def provider_cache_tag(
        runtime: Any,
        *,
        request_base_url: str | None,
        request_model: str | None,
        byok: bool,
    ) -> str:
        capabilities = getattr(runtime, "capabilities", None)
        model = (
            request_model
            or getattr(capabilities, "model", None)
            or os.getenv("SWUFE_RAG_LLM_MODEL")
            or os.getenv("OPENAI_MODEL")
            or "deterministic"
        )
        base_url = (
            request_base_url
            or os.getenv("SWUFE_RAG_LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "local"
        )
        endpoint_hash = hashlib.sha256(base_url.encode("utf-8")).hexdigest()[:16]
        kind = "byok" if byok else (
            "server-llm"
            if bool(getattr(capabilities, "general_llm", False))
            else "local"
        )
        return f"{kind}:{model}:{endpoint_hash}"

    def answer_cache_key(
        cache: Any,
        runtime: Any,
        request: AskRequest,
        *,
        request_base_url: str | None,
        request_model: str | None,
        byok: bool,
    ) -> str:
        return cache.build_key(
            request.question,
            request.college,
            request.cohort,
            request.major,
            provider_cache_tag(
                runtime,
                request_base_url=request_base_url,
                request_model=request_model,
                byok=byok,
            ),
            namespace=get_cache_namespace(runtime),
        )

    def hydrate_cached_context(
        runtime: Any, request: AskRequest, payload: dict[str, Any]
    ) -> bool:
        if request.session_id is None:
            return True
        recorder = getattr(runtime, "record_cached_response", None)
        return bool(
            callable(recorder)
            and recorder(
                request.question,
                payload,
                session_id=request.session_id,
            )
        )

    def mark_cache_result(
        payload: dict[str, Any], *, hit: bool, started_at: float
    ) -> dict[str, Any]:
        result = dict(payload)
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        cache_meta: dict[str, Any] = {"hit": hit, "lookup_ms": elapsed_ms}
        if hit and isinstance(result.get("latency_ms"), (int, float)):
            cache_meta["origin_latency_ms"] = result["latency_ms"]
            result["latency_ms"] = elapsed_ms
        result["answer_cache"] = cache_meta
        return result

    def get_audit():
        with lock:
            if state["audit"] is None:
                state["audit"] = CurriculumAuditService(
                    os.getenv("SWUFE_RAG_CURRICULUM", "data/curriculum_catalog_v2.json")
                )
            return state["audit"]

    product = FastAPI(title="swufe-rag typed query API", version="0.4.0", redoc_url=None)

    @product.middleware("http")
    async def dependency_gate(request: Request, call_next):
        path = request.url.path
        workload = (
            path in {"/ask", "/ask/stream", "/academic-audit", "/options"}
            or path.startswith("/source/")
        )
        if workload and redis_required():
            redis = required_redis_health()
            if redis.get("reachable") is not True:
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": {
                            "code": "redis_required_unavailable",
                            "message": "共享会话服务暂时不可用，请稍后重试。",
                        }
                    },
                    headers={"Retry-After": "2"},
                )
        eager = (os.getenv("SWUFE_RAG_EAGER_WARMUP") or "").strip() == "1"
        if workload and eager:
            with lock:
                runtime_loaded = state["runtime"] is not None
                warmup_error = state["warmup_error"]
            if not runtime_loaded:
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": {
                            "code": "runtime_not_ready",
                            "message": "知识库运行时仍在预热，请稍后重试。",
                            "warmup_failed": warmup_error is not None,
                        }
                    },
                    headers={"Retry-After": "2"},
                )
        return await call_next(request)

    @product.middleware("http")
    async def throttle(request: Request, call_next):
        """Per-client 限流。BYOK 下别人花的是自己的 Key,但检索烧的是本机
        算力,公网暴露必须有闸门。探针豁免,超限返回 429 + Retry-After。"""
        limiter: RateLimiter = state["rate_limiter"]
        if limiter.enabled and request.url.path not in EXEMPT_PATHS:
            allowed, retry_after = limiter.check(client_identity(request))
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": (
                            f"请求过于频繁,请在 {retry_after} 秒后重试"
                            f"(每 {limiter.window_seconds} 秒最多 {limiter.limit} 次)。"
                        )
                    },
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)

    @product.on_event("startup")
    def eager_warmup() -> None:
        """可选预热:后台线程加载模型与索引,期间 /readyz 保持 503。"""
        if (os.getenv("SWUFE_RAG_EAGER_WARMUP") or "").strip() != "1":
            return

        def load() -> None:
            try:
                get_runtime()
            except Exception as exc:
                # 预热失败必须让 /readyz 明确暴露,而不是留到首个用户请求才炸。
                with lock:
                    state["warmup_error"] = f"{type(exc).__name__}: {exc}"
                logging.getLogger(__name__).exception("eager warmup failed")

        Thread(target=load, name="swufe-warmup", daemon=True).start()

    @product.get("/healthz", include_in_schema=False)
    def healthz():
        """Cheap liveness probe that does not load the retrieval models."""
        return {"status": "ok"}

    @product.get("/readyz", include_in_schema=False)
    def readyz():
        """就绪探针:数据资产齐备、且(开启预热时)运行时已加载才算就绪。

        不会主动触发模型加载——否则首次探测就会挂住数秒被判超时。
        开启 SWUFE_RAG_EAGER_WARMUP=1 时,加载完成前保持 503,
        负载均衡器便不会把请求打到还在冷启动的实例上。
        """
        with lock:
            runtime_loaded = state["runtime"] is not None
            warmup_error = state["warmup_error"]
        eager = (os.getenv("SWUFE_RAG_EAGER_WARMUP") or "").strip() == "1"
        report = readiness_report(
            # 未开启预热时不把"尚未加载"当作未就绪(保持懒加载语义)。
            runtime_loaded=runtime_loaded or not eager,
            warmup_error=warmup_error,
        )
        report["eager_warmup"] = eager
        report["runtime_loaded"] = runtime_loaded
        report["rate_limit"] = state["rate_limiter"].info()
        if not report["ready"]:
            return JSONResponse(status_code=503, content=report)
        return report

    @product.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "chat.html")

    @product.get("/academic-audit-ui", include_in_schema=False)
    def audit_ui():
        return FileResponse(STATIC_DIR / "academic_audit.html")

    @product.get("/assets/chat.css", include_in_schema=False)
    def chat_css():
        return FileResponse(STATIC_DIR / "chat.css", media_type="text/css")

    @product.get("/assets/chat.js", include_in_schema=False)
    def chat_js():
        return FileResponse(
            STATIC_DIR / "chat.js", media_type="application/javascript"
        )

    @product.get("/options")
    def options():
        try:
            runtime = get_runtime()
            value = runtime.options()
            value["redis"] = {
                "sessions": component_info(getattr(runtime, "sessions", None)),
                "answer_cache": component_info(get_answer_cache()),
            }
            value["query_capacity"] = get_query_capacity().info()
            return value
        except (ContractError, KnowledgeBaseNotReadyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    def provider_client(
        api_key: str | None,
        base_url: str | None,
        model: str | None,
    ) -> OpenAICompatibleClient:
        if not isinstance(api_key, str) or not api_key.strip():
            raise HTTPException(status_code=400, detail="X-LLM-API-Key must not be blank")
        clean_base_url = validate_request_llm_base_url(base_url)
        if clean_base_url is None:
            raise HTTPException(status_code=400, detail="X-LLM-Base-URL is required")
        clean_model = model.strip() if isinstance(model, str) else ""
        if not clean_model:
            clean_model = "provider-model-discovery"
        return OpenAICompatibleClient(
            clean_model,
            base_url=clean_base_url,
            api_key=api_key.strip(),
            max_retries=0,
            timeout_seconds=20,
        )

    def provider_http_error(exc: GenerationUnavailableError) -> HTTPException:
        code = getattr(exc, "code", "provider_unavailable")
        status = {
            "provider_authentication_failed": 401,
            "provider_permission_denied": 403,
            "provider_model_not_found": 404,
            "provider_rate_limited": 429,
            "provider_timeout": 504,
        }.get(code, 502)
        return HTTPException(
            status_code=status,
            detail={"code": code, "message": str(exc)},
        )

    @product.post("/llm/models", response_model=LLMModelsResponse)
    def llm_models(
        x_llm_api_key: str | None = Header(default=None, alias="X-LLM-API-Key", max_length=512),
        x_llm_base_url: str | None = Header(default=None, alias="X-LLM-Base-URL", max_length=512),
    ):
        try:
            client = provider_client(x_llm_api_key, x_llm_base_url, None)
            return LLMModelsResponse(models=client.list_models())
        except GenerationUnavailableError as exc:
            raise provider_http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @product.post("/llm/validate", response_model=LLMValidationResponse)
    def llm_validate(
        x_llm_api_key: str | None = Header(default=None, alias="X-LLM-API-Key", max_length=512),
        x_llm_base_url: str | None = Header(default=None, alias="X-LLM-Base-URL", max_length=512),
        x_llm_model: str | None = Header(default=None, alias="X-LLM-Model", max_length=256),
    ):
        try:
            client = provider_client(x_llm_api_key, x_llm_base_url, x_llm_model)
            if x_llm_model is None or not x_llm_model.strip():
                raise HTTPException(status_code=400, detail="X-LLM-Model is required")
            client.generate(
                "你是模型连接测试。",
                "只回复两个大写英文字母 OK，不要输出其他内容。",
            )
            return LLMValidationResponse(valid=True, model=x_llm_model.strip())
        except GenerationUnavailableError as exc:
            raise provider_http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @product.post("/ask")
    def ask(
        request: AskRequest,
        x_llm_api_key: str | None = Header(default=None, alias="X-LLM-API-Key", max_length=512),
        x_llm_base_url: str | None = Header(default=None, alias="X-LLM-Base-URL", max_length=512),
        x_llm_model: str | None = Header(default=None, alias="X-LLM-Model", max_length=256),
    ):
        request_started = time.perf_counter()
        try:
            web_sources: list[dict[str, str]] = []
            web_context = None
            selected = get_runtime()
            if x_llm_api_key is not None:
                selected = build_request_query_runtime(
                    selected,
                    x_llm_api_key,
                    config_path=os.getenv("SWUFE_RAG_CONFIG", "config.advanced.yaml"),
                    base_url=x_llm_base_url,
                    model_override=x_llm_model,
                    thinking_enabled=request.deep_thinking,
                )
            stage_managed_capacity = bind_retrieval_capacity(selected)
            sessions = getattr(selected, "sessions", None)
            with session_guard(sessions, request.session_id):
                # Only context-free turns may read/write the shared answer cache.
                cache = get_answer_cache()
                cache_key = None
                if (
                    cache is not None
                    and not request.deep_thinking
                    and not request.web_search
                    and not session_has_history(
                    sessions, request.session_id
                    )
                ):
                    cache_key = answer_cache_key(
                        cache,
                        selected,
                        request,
                        request_base_url=x_llm_base_url,
                        request_model=x_llm_model,
                        byok=x_llm_api_key is not None,
                    )
                    cached = cache.get(cache_key)
                    if cached is not None and hydrate_cached_context(
                        selected, request, cached
                    ):
                        return mark_cache_result(
                            cached, hit=True, started_at=request_started
                        )

                can_stream_general = getattr(selected, "can_stream_general", None)
                direct_general = bool(
                    callable(can_stream_general)
                    and can_stream_general(
                        request.question,
                        college=request.college,
                        cohort=request.cohort,
                        major=request.major,
                        session_id=request.session_id,
                    )
                )
                if request.web_search and direct_general:
                    web_sources = list(web_searcher(request.question))
                    web_context = format_web_context(web_sources)

                def execute_payload():
                    web_options = (
                        {"web_context": web_context, "web_sources": web_sources}
                        if request.web_search
                        else {}
                    )
                    payload = selected.handle_question(
                        request.question,
                        college=request.college,
                        cohort=request.cohort,
                        major=request.major,
                        session_id=request.session_id,
                        **web_options,
                    )
                    payload = add_school_web_fallback(
                        selected,
                        request,
                        payload,
                    )
                    if (
                        cache is not None
                        and cache_key is not None
                        and cacheable_answer(request.question, payload)
                    ):
                        cache.put(cache_key, payload)
                    return (
                        mark_cache_result(payload, hit=False, started_at=request_started)
                        if cache is not None and isinstance(payload, dict)
                        else payload
                    )

                if direct_general:
                    return execute_payload()

                # Typed runtimes acquire capacity around BGE retrieval only.
                # Keep the old whole-request guard for legacy test/runtime
                # adapters that do not expose the stage hook.
                if stage_managed_capacity:
                    if cache is not None and cache_key is not None:
                        cached = cache.get(cache_key)
                        if cached is not None and hydrate_cached_context(
                            selected, request, cached
                        ):
                            return mark_cache_result(
                                cached, hit=True, started_at=request_started
                            )
                    return execute_payload()
                with get_query_capacity().acquire():
                    if cache is not None and cache_key is not None:
                        cached = cache.get(cache_key)
                        if cached is not None and hydrate_cached_context(
                            selected, request, cached
                        ):
                            return mark_cache_result(
                                cached, hit=True, started_at=request_started
                            )
                    return execute_payload()
        except (
            ContractError,
            KnowledgeBaseNotReadyError,
            GenerationUnavailableError,
            SessionLockTimeoutError,
            RedisUnavailableError,
            FileNotFoundError,
        ) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except QueryCapacityError as exc:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "query_capacity_exhausted",
                    "reason": exc.code,
                    "message": "当前检索请求较多，请稍后重试。",
                    "active": exc.active,
                    "waiting": exc.waiting,
                },
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc

    @product.post("/ask/stream")
    def ask_stream(
        request: AskRequest,
        x_llm_api_key: str | None = Header(
            default=None, alias="X-LLM-API-Key", max_length=512
        ),
        x_llm_base_url: str | None = Header(
            default=None, alias="X-LLM-Base-URL", max_length=512
        ),
        x_llm_model: str | None = Header(
            default=None, alias="X-LLM-Model", max_length=256
        ),
    ):
        try:
            web_sources: list[dict[str, str]] = []
            web_context = None
            base_runtime = get_runtime()
            selected = base_runtime
            if x_llm_api_key is not None:
                selected = build_request_query_runtime(
                    base_runtime,
                    x_llm_api_key,
                    config_path=os.getenv(
                        "SWUFE_RAG_CONFIG", "config.advanced.yaml"
                    ),
                    base_url=x_llm_base_url,
                    model_override=x_llm_model,
                    thinking_enabled=request.deep_thinking,
                )
            # The selected BYOK runtime owns the per-request model clients while
            # sharing retrieval assets and sessions with the base runtime.
            stage_managed_capacity = bind_retrieval_capacity(base_runtime)
            if selected is not base_runtime:
                stage_managed_capacity = (
                    bind_retrieval_capacity(selected) or stage_managed_capacity
                )
        except (
            ContractError,
            KnowledgeBaseNotReadyError,
            GenerationUnavailableError,
            FileNotFoundError,
        ) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        def line(event: dict[str, Any]) -> str:
            return json.dumps(
                jsonable_encoder(event),
                ensure_ascii=False,
                separators=(",", ":"),
            ) + "\n"

        def text_chunks(value: str, size: int = 24) -> Iterator[str]:
            for start in range(0, len(value), size):
                yield value[start : start + size]

        def events() -> Iterator[str]:
            lease = None
            web_sources: list[dict[str, str]] = []
            web_context = None
            try:
                stream_started = time.perf_counter()
                sessions = getattr(selected, "sessions", None)
                with session_guard(sessions, request.session_id):
                    direct_general = selected.can_stream_general(
                        request.question,
                        college=request.college,
                        cohort=request.cohort,
                        major=request.major,
                        session_id=request.session_id,
                    )
                    if direct_general:
                        if request.web_search:
                            yield line({
                                "type": "status",
                                "stage": "web_search",
                                "message": "正在搜索公开网页资料",
                            })
                            web_sources = list(web_searcher(request.question))
                            web_context = format_web_context(web_sources)
                        for event in selected.stream_general_question(
                            request.question,
                            college=request.college,
                            cohort=request.cohort,
                            major=request.major,
                            session_id=request.session_id,
                            web_context=web_context,
                            web_sources=web_sources,
                        ):
                            yield line(event)
                        return

                    cache = get_answer_cache()
                    cache_key = None
                    payload = None
                    if (
                        cache is not None
                        and not request.deep_thinking
                        and not request.web_search
                        and not session_has_history(
                        sessions, request.session_id
                        )
                    ):
                        cache_key = answer_cache_key(
                            cache,
                            selected,
                            request,
                            request_base_url=x_llm_base_url,
                            request_model=x_llm_model,
                            byok=x_llm_api_key is not None,
                        )
                        cached = cache.get(cache_key)
                        if cached is not None and hydrate_cached_context(
                            selected, request, cached
                        ):
                            payload = mark_cache_result(
                                cached, hit=True, started_at=stream_started
                            )
                            yield line(
                                {
                                    "type": "status",
                                    "stage": "cache",
                                    "message": "已命中可信答案缓存",
                                }
                            )

                    if payload is None:
                        yield line(
                            {
                                "type": "status",
                                "stage": "retrieving",
                                "message": "正在检索并校验学校证据",
                            }
                        )
                        if not stage_managed_capacity:
                            lease = get_query_capacity().acquire()
                        # Another request may have filled the shared cache while
                        # this turn waited for capacity. Avoid a cache stampede.
                        if cache is not None and cache_key is not None:
                            cached = cache.get(cache_key)
                            if cached is not None and hydrate_cached_context(
                                selected, request, cached
                            ):
                                payload = mark_cache_result(
                                    cached, hit=True, started_at=stream_started
                                )
                                yield line(
                                    {
                                        "type": "status",
                                        "stage": "cache",
                                        "message": "已命中可信答案缓存",
                                    }
                                )
                        if payload is None:
                            # School answers remain fail-closed: no text is
                            # emitted before evidence and citation validation.
                            web_options = (
                                {"web_context": web_context, "web_sources": web_sources}
                                if request.web_search
                                else {}
                            )
                            payload = selected.handle_question(
                                request.question,
                                college=request.college,
                                cohort=request.cohort,
                                major=request.major,
                                session_id=request.session_id,
                                **web_options,
                            )
                            if _needs_school_web_fallback(
                                selected, payload, request.question
                            ):
                                yield line({
                                    "type": "status",
                                    "stage": "web_search",
                                    "message": "校内依据不足，正在搜索公开网页资料",
                                })
                                payload = add_school_web_fallback(
                                    selected,
                                    request,
                                    payload,
                                )
                            if (
                                cache is not None
                                and cache_key is not None
                                and cacheable_answer(request.question, payload)
                            ):
                                cache.put(cache_key, payload)
                            if cache is not None:
                                payload = mark_cache_result(
                                    payload, hit=False, started_at=stream_started
                                )

                    yield line(
                        {
                            "type": "meta",
                            "mode": payload.get("mode"),
                            "execution_path": payload.get("execution_path"),
                            "answer_streaming": True,
                        }
                    )
                    preview = _stream_preview_text(
                        str(payload.get("answer_md") or "")
                    )
                    for chunk in text_chunks(preview):
                        yield line({"type": "delta", "text": chunk})
                    yield line({"type": "final", "response": payload})
            except GeneratorExit:
                return
            except (
                ContractError,
                KnowledgeBaseNotReadyError,
                GenerationUnavailableError,
                SessionLockTimeoutError,
                RedisUnavailableError,
                FileNotFoundError,
                ValueError,
            ) as exc:
                yield line(
                    {
                        "type": "error",
                        "message": str(exc),
                        "error_type": type(exc).__name__,
                        "error_code": getattr(exc, "code", None),
                    }
                )
            except QueryCapacityError as exc:
                yield line(
                    {
                        "type": "error",
                        "message": "当前检索请求较多，请稍后重试。",
                        "error_type": "QueryCapacityError",
                        "reason": exc.code,
                        "retry_after_seconds": exc.retry_after_seconds,
                    }
                )
            except Exception as exc:
                yield line(
                    {
                        "type": "error",
                        "message": "流式回答暂时不可用，请稍后重试。",
                        "error_type": type(exc).__name__,
                    }
                )
            finally:
                if lease is not None:
                    lease.release()

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @product.get("/source/{chunk_id}")
    def source(chunk_id: str):
        value = get_runtime().source(chunk_id)
        if value is None:
            raise HTTPException(status_code=404, detail="chunk_id not found")
        return value

    @product.get("/academic-audit/options")
    def audit_options():
        try:
            return get_audit().options()
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @product.post("/academic-audit")
    def academic_audit(request: AcademicAuditRequest):
        completed = [
            item if isinstance(item, str) else item.model_dump(exclude_none=True)
            for item in request.completed_courses
        ]
        try:
            service = get_audit()
            if request.question:
                return service.audit_question(
                    request.question,
                    cohort=request.cohort,
                    major=request.major,
                    completed_courses=completed,
                    target_module=request.target_module,
                    current_semester=request.current_semester,
                )
            if not request.cohort or not request.major:
                raise ValueError("cohort and major are required")
            return service.audit(
                cohort=request.cohort,
                major=request.major,
                completed_courses=completed,
                target_module=request.target_module,
                current_semester=request.current_semester,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return product


app = create_app()


def main() -> None:
    import uvicorn

    # 默认仍绑回环(本机开发安全);容器内需 SWUFE_RAG_HOST=0.0.0.0 才能被
    # 外部访问,这一步显式化避免开发环境无意暴露到公网。
    uvicorn.run(
        "app.server.application:app",
        host=os.getenv("SWUFE_RAG_HOST", "127.0.0.1"),
        port=int(os.getenv("SWUFE_RAG_PORT", "8000")),
        workers=int(os.getenv("SWUFE_RAG_WORKERS", "1")),
        # 反代后面必须开启,否则拿到的 client.host 永远是 Nginx 的地址。
        proxy_headers=(os.getenv("SWUFE_RAG_TRUST_PROXY") or "").strip() == "1",
        forwarded_allow_ips=os.getenv("SWUFE_RAG_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        reload=False,
    )


__all__ = [
    "AcademicAuditRequest",
    "AskRequest",
    "_stream_preview_text",
    "app",
    "create_app",
    "main",
]
