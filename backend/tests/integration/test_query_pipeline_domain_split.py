from __future__ import annotations

from pathlib import Path

from academic_audit.database import AcademicDatabase
from contracts import GenerationUnavailableError, RETRIEVED_CHUNK_FIELDS
from generation.answer_presenter import AnswerPresenter
from generation.general_chat import GeneralChatService
from retrieval.index import load_chunks
from storage.metadata_db import MetadataDB
from swufe_rag.query_pipeline import PipelineCapabilities, QueryPipelineRuntime
from swufe_rag.redis_support import RedisSessionStore
from swufe_rag.query_understanding import QuestionUnderstandingService
from swufe_rag.routing.router import HybridRouter


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "chunks.jsonl"


class RecordingGeneralClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return "这是通用大模型的回答。"


class ContentFilteredStreamingClient:
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise AssertionError("streaming path expected")

    def stream_generate(self, system_prompt: str, user_prompt: str):
        yield "不应保留的半截回答"
        raise GenerationUnavailableError(
            "当前模型服务因内容安全策略拒绝处理这条问题。",
            code="provider_content_filtered",
        )


class RecordingRetriever:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, question: str, **scope):
        self.calls.append((question, scope))
        return []


class SharedFakeRedis:
    """Minimal shared Redis view for cross-runtime session tests."""

    def __init__(self, data: dict[str, str]) -> None:
        self.data = data

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value
        return True

    def exists(self, key):
        return int(key in self.data)


def test_authoritative_metadata_chunks_keep_the_full_retrieval_contract() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=RecordingRetriever(),
        school_answer=lambda *_: {},
        general_chat=GeneralChatService(RecordingGeneralClient()),
        metadata_db=metadata,
        runtime_mode="metadata-contract-test",
    )
    try:
        values = runtime._metadata_chunks(limit=1)
    finally:
        metadata.close()

    assert values
    assert set(values[0]) == set(RETRIEVED_CHUNK_FIELDS)
    assert isinstance(values[0]["year"], int)
    assert isinstance(values[0]["is_table"], bool)


def test_general_task_calls_general_model_once_and_never_retrieves() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    client = RecordingGeneralClient()
    retriever = RecordingRetriever()
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        capabilities=PipelineCapabilities(general_llm=True, model="test-chat"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=retriever,
        school_answer=lambda *_: (_ for _ in ()).throw(AssertionError("school answer called")),
        general_chat=GeneralChatService(client),
        metadata_db=metadata,
        runtime_mode="domain-split-test",
    )
    try:
        result = runtime.handle_question("帮我写一个Python选课系统")
    finally:
        metadata.close()

    assert result["mode"] == "general_chat"
    assert result["execution_path"] == "general_llm"
    assert result["final_output_source"] == "llm"
    assert result["llm_called"] is True
    assert result["llm_stages"]["general_generation"] is True
    assert result["citations"] == []
    assert result["retrieved"] == []
    assert len(client.calls) == 1
    assert retriever.calls == []


def test_sync_general_client_emits_only_a_final_event() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    client = RecordingGeneralClient()
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        capabilities=PipelineCapabilities(general_llm=True, model="test-chat"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=RecordingRetriever(),
        school_answer=lambda *_: (_ for _ in ()).throw(
            AssertionError("school answer called")
        ),
        general_chat=GeneralChatService(client),
        metadata_db=metadata,
        runtime_mode="sync-general-stream-test",
    )
    try:
        events = list(runtime.stream_general_question("帮我写一段生日祝福"))
    finally:
        metadata.close()

    assert events[0]["type"] == "meta"
    assert events[0]["answer_streaming"] is False
    assert not [event for event in events if event["type"] == "delta"]
    assert events[-1]["type"] == "final"
    assert events[-1]["response"]["answer_md"] == "这是通用大模型的回答。"
    assert len(client.calls) == 1


def test_general_content_filter_replaces_partial_stream_with_safe_refusal() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        capabilities=PipelineCapabilities(general_llm=True, model="test-chat"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=RecordingRetriever(),
        school_answer=lambda *_: (_ for _ in ()).throw(
            AssertionError("school answer called")
        ),
        general_chat=GeneralChatService(ContentFilteredStreamingClient()),
        metadata_db=metadata,
        runtime_mode="content-filter-stream-test",
    )
    try:
        events = list(
            runtime.stream_general_question(
                "介绍一个历史事件",
                session_id="content-filter-session",
            )
        )
        history = runtime.sessions.get("content-filter-session").general_history
    finally:
        metadata.close()

    reset = next(event for event in events if event["type"] == "reset")
    final = events[-1]["response"]
    assert "不是网络或教务后端故障" in reset["text"]
    assert final["answer_md"] == reset["text"]
    assert final["refused"] is False
    assert final["fallback_reason"] == "provider_content_filtered"
    assert final["final_output_source"] == "provider_policy_refusal"
    assert history == []


def test_school_fact_never_falls_back_to_general_model() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    client = RecordingGeneralClient()
    retriever = RecordingRetriever()
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        capabilities=PipelineCapabilities(general_llm=True, model="test-chat"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=retriever,
        school_answer=lambda *_: (_ for _ in ()).throw(AssertionError("no chunks")),
        general_chat=GeneralChatService(client),
        metadata_db=metadata,
        runtime_mode="domain-split-test",
    )
    try:
        result = runtime.handle_question("校园网密码忘了怎么办？")
    finally:
        metadata.close()

    assert result["mode"] == "school_rag"
    assert result["execution_path"] == "rag"
    assert result["refused"] is True
    assert len(retriever.calls) == 1
    assert client.calls == []


def test_recognized_policy_uses_authoritative_metadata_without_vector_retrieval() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    authoritative = {
        **chunks[0],
        "chunk_id": "authoritative-buffer-rule",
        "doc_title": "西南财经大学本科学生缓考规定",
        "article": "第三条 缓考申请",
        "text": "学生因病不能参加考试时，应按学校规定提交缓考申请。",
    }
    metadata = MetadataDB.from_chunks(
        [*chunks, authoritative], trusted_by_default=True
    )
    retriever = RecordingRetriever()
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        capabilities=PipelineCapabilities(),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=retriever,
        school_answer=lambda *_args, **_kwargs: {
            "answer_md": "证据不足",
            "citations": [],
            "refused": True,
        },
        general_chat=GeneralChatService(RecordingGeneralClient()),
        metadata_db=metadata,
        runtime_mode="authoritative-policy-fast-path-test",
    )
    try:
        result = runtime.handle_question("生病了怎么申请缓考？")
    finally:
        metadata.close()

    assert result["execution_path"] == "rag"
    assert result["rag"]["retrieval_source"] == "authoritative_metadata"
    assert result["rag"]["retrieval_ms"] < 100
    assert retriever.calls == []


def test_school_overview_metadata_miss_skips_semantic_retrieval() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    retriever = RecordingRetriever()
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        capabilities=PipelineCapabilities(general_llm=True, model="test-chat"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=retriever,
        school_answer=lambda *_: (_ for _ in ()).throw(AssertionError("no chunks")),
        general_chat=GeneralChatService(RecordingGeneralClient()),
        metadata_db=metadata,
        runtime_mode="school-overview-fast-miss-test",
    )
    try:
        result = runtime.handle_question("给我介绍一下你们西南财经大学呗")
    finally:
        metadata.close()

    assert result["mode"] == "school_rag"
    assert result["refused"] is True
    assert result["rag"]["retrieval_source"] == "authoritative_profile_unavailable"
    assert result["rag"]["retrieval_ms"] < 100
    assert retriever.calls == []


def test_elliptical_follow_up_reuses_school_context_instead_of_general_model() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    client = RecordingGeneralClient()
    retriever = RecordingRetriever()
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        capabilities=PipelineCapabilities(general_llm=True, model="test-chat"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=retriever,
        school_answer=lambda *_: (_ for _ in ()).throw(AssertionError("no chunks")),
        general_chat=GeneralChatService(client),
        metadata_db=metadata,
        runtime_mode="domain-split-test",
    )
    try:
        runtime.handle_question(
            "生病了怎么申请缓考？", session_id="school-follow-up"
        )
        result = runtime.handle_question(
            "那需要准备哪些材料？", session_id="school-follow-up"
        )
    finally:
        metadata.close()

    assert result["mode"] == "school_rag"
    assert result["execution_path"] == "rag"
    assert result["normalized_query"]["primary_intent"] == "policy"
    assert "生病了怎么申请缓考" in retriever.calls[-1][0]
    assert "那需要准备哪些材料" in retriever.calls[-1][0]
    assert client.calls == []


def test_school_follow_up_context_survives_a_different_runtime_instance() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    shared_data: dict[str, str] = {}
    first_retriever = RecordingRetriever()
    second_retriever = RecordingRetriever()
    first_client = RecordingGeneralClient()
    second_client = RecordingGeneralClient()

    def runtime_for(retriever, client) -> QueryPipelineRuntime:
        return QueryPipelineRuntime(
            understanding=QuestionUnderstandingService(),
            presenter=AnswerPresenter(),
            academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
            capabilities=PipelineCapabilities(general_llm=True, model="test-chat"),
            router=HybridRouter(known_colleges=metadata.known_colleges()),
            school_retrieve=retriever,
            school_answer=lambda *_: (_ for _ in ()).throw(
                AssertionError("no chunks")
            ),
            general_chat=GeneralChatService(client),
            metadata_db=metadata,
            sessions=RedisSessionStore(SharedFakeRedis(shared_data), ttl_seconds=60),
            runtime_mode="cross-instance-context-test",
        )

    first = runtime_for(first_retriever, first_client)
    second = runtime_for(second_retriever, second_client)
    try:
        first.handle_question("生病了怎么申请缓考？", session_id="shared-session")
        result = second.handle_question(
            "那需要准备哪些材料？", session_id="shared-session"
        )
    finally:
        metadata.close()

    assert result["mode"] == "school_rag"
    assert result["execution_path"] == "rag"
    assert "生病了怎么申请缓考" in second_retriever.calls[-1][0]
    assert "那需要准备哪些材料" in second_retriever.calls[-1][0]
    assert first_client.calls == []
    assert second_client.calls == []


def test_acknowledgement_does_not_erase_school_follow_up_context() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    client = RecordingGeneralClient()
    retriever = RecordingRetriever()
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        capabilities=PipelineCapabilities(general_llm=True, model="test-chat"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=retriever,
        school_answer=lambda *_: (_ for _ in ()).throw(AssertionError("no chunks")),
        general_chat=GeneralChatService(client),
        metadata_db=metadata,
        runtime_mode="domain-split-test",
    )
    try:
        runtime.handle_question("生病了怎么申请缓考？", session_id="ack-follow-up")
        acknowledgement = runtime.handle_question("谢谢你", session_id="ack-follow-up")
        result = runtime.handle_question(
            "那需要准备哪些材料？", session_id="ack-follow-up"
        )
    finally:
        metadata.close()

    assert acknowledgement["mode"] == "general_chat"
    assert result["mode"] == "school_rag"
    assert result["execution_path"] == "rag"
    assert "生病了怎么申请缓考" in retriever.calls[-1][0]
    assert "那需要准备哪些材料" in retriever.calls[-1][0]


def test_natural_school_follow_up_variants_reuse_policy_context() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    client = RecordingGeneralClient()
    retriever = RecordingRetriever()
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        capabilities=PipelineCapabilities(general_llm=True, model="test-chat"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=retriever,
        school_answer=lambda *_: (_ for _ in ()).throw(AssertionError("no chunks")),
        general_chat=GeneralChatService(client),
        metadata_db=metadata,
        runtime_mode="domain-split-test",
    )
    follow_ups = (
        "总结一下",
        "帮我总结一下",
        "换个简单说法",
        "能说详细点吗？",
        "还有别的条件吗？",
        "这条规定对大一也适用吗？",
        "它适用于补考吗？",
        "如果来不及申请呢？",
        "最晚什么时候申请？",
        "申请材料有哪些？",
        "缓考需要哪些证明？",
    )
    try:
        for index, follow_up in enumerate(follow_ups):
            session_id = f"school-follow-up-variant-{index}"
            runtime.handle_question("生病了怎么申请缓考？", session_id=session_id)
            result = runtime.handle_question(follow_up, session_id=session_id)
            assert result["mode"] == "school_rag", follow_up
            assert result["execution_path"] == "rag", follow_up
            if result["rag"].get("retrieval_source") == "semantic_retrieval":
                assert "生病了怎么申请缓考" in retriever.calls[-1][0], follow_up
                assert follow_up.rstrip("？") in retriever.calls[-1][0], follow_up
            else:
                assert result["rag"]["retrieval_source"] == "authoritative_metadata"
    finally:
        metadata.close()

    assert client.calls == []


def test_curriculum_follow_up_inherits_major_scope() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        capabilities=PipelineCapabilities(),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=RecordingRetriever(),
        school_answer=lambda *_: {"answer_md": "", "citations": [], "refused": True},
        general_chat=GeneralChatService(RecordingGeneralClient()),
        metadata_db=metadata,
        runtime_mode="curriculum-follow-up-scope-test",
    )
    try:
        runtime.handle_question(
            "2023级人工智能专业的培养方案有哪些课程？",
            college="计算机与人工智能学院",
            cohort="2023",
            major="人工智能专业",
            session_id="curriculum-scope-follow-up",
        )
        result = runtime.handle_question(
            "其中专业选修课需要多少学分？",
            session_id="curriculum-scope-follow-up",
        )
    finally:
        metadata.close()

    assert result["execution_path"] != "clarify"
    assert result["normalized_query"]["major"] == "人工智能专业"
    assert "major" not in result["normalized_query"]["missing_fields"]


def test_module_credit_follow_up_returns_the_requested_requirement() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        capabilities=PipelineCapabilities(),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=RecordingRetriever(),
        school_answer=lambda *_: {"answer_md": "", "citations": [], "refused": True},
        general_chat=GeneralChatService(RecordingGeneralClient()),
        metadata_db=metadata,
        runtime_mode="module-credit-follow-up-test",
    )
    try:
        runtime.handle_question(
            "毕业需要修满多少学分？",
            college="计算机与人工智能学院",
            cohort="2024",
            major="网络空间安全专业",
            session_id="module-credit-follow-up",
        )
        result = runtime.handle_question(
            "其中专业选修课最低需要多少学分？",
            session_id="module-credit-follow-up",
        )
    finally:
        metadata.close()

    assert result["execution_path"] == "sql"
    assert result["normalized_query"]["primary_intent"] == "graduation_requirement"
    assert result["normalized_query"]["course_modules"] == ["专业选修课"]
    assert "8" in result["answer_md"]


def test_school_turn_breaks_stale_general_model_history() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    client = RecordingGeneralClient()
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        capabilities=PipelineCapabilities(general_llm=True, model="test-chat"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=RecordingRetriever(),
        school_answer=lambda *_: (_ for _ in ()).throw(AssertionError("no chunks")),
        general_chat=GeneralChatService(client),
        metadata_db=metadata,
        runtime_mode="domain-split-test",
    )
    try:
        runtime.handle_question("为什么天空是蓝色的？", session_id="history-boundary")
        runtime.handle_question("生病了怎么申请缓考？", session_id="history-boundary")
        acknowledgement = runtime.handle_question("谢谢你", session_id="history-boundary")
    finally:
        metadata.close()

    assert acknowledgement["mode"] == "general_chat"
    assert len(client.calls) == 2
    acknowledgement_prompt = client.calls[-1][1]
    assert "为什么天空是蓝色" not in acknowledgement_prompt
    assert acknowledgement_prompt.endswith("谢谢你")


def test_substantive_general_task_clears_old_school_context() -> None:
    chunks = load_chunks(FIXTURE_PATH)
    metadata = MetadataDB.from_chunks(chunks, trusted_by_default=True)
    client = RecordingGeneralClient()
    retriever = RecordingRetriever()
    runtime = QueryPipelineRuntime(
        understanding=QuestionUnderstandingService(),
        presenter=AnswerPresenter(),
        academic_db=AcademicDatabase("data/academic_v2.sqlite3"),
        capabilities=PipelineCapabilities(general_llm=True, model="test-chat"),
        router=HybridRouter(known_colleges=metadata.known_colleges()),
        school_retrieve=retriever,
        school_answer=lambda *_: (_ for _ in ()).throw(AssertionError("no chunks")),
        general_chat=GeneralChatService(client),
        metadata_db=metadata,
        runtime_mode="domain-split-test",
    )
    try:
        runtime.handle_question("生病了怎么申请缓考？", session_id="clear-follow-up")
        runtime.handle_question("帮我写一段生日祝福", session_id="clear-follow-up")
        result = runtime.handle_question(
            "那需要准备哪些材料？", session_id="clear-follow-up"
        )
    finally:
        metadata.close()

    assert result["mode"] == "general_chat"
    assert result["execution_path"] == "general_llm"
