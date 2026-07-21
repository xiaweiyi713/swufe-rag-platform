"""模块D:FastAPI 后端入口。

启动(仓库根目录):
    uvicorn app.server:app --host 127.0.0.1 --port 8000

对外接口(契约4 + 模块D扩展,详见主 README):
    POST /ask                -> {answer_md, citations, retrieved, latency_ms, refused}
    GET  /source/{chunk_id}  -> 知识块完整原文(契约1格式)
    GET  /meta               -> {colleges, cohorts}(约定 D-5,前端下拉框数据源)
    GET  /                   -> 前端页面 app/static/index.html
"""
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.providers import get_providers

ROOT = Path(__file__).resolve().parent.parent
SNIPPET_LEN = 120  # 约定 D-4:retrieved 摘要长度


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    college: Optional[str] = None
    cohort: Optional[str] = None


def _load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_chunk_index(config: dict) -> dict:
    """chunk_id -> 完整知识块,供 /source 与 /meta 使用。

    mock 模式读 mock/mock_chunks.jsonl;real 模式读模块A的 chunks_path。
    """
    if config.get("provider", "mock") == "mock":
        path = ROOT / config.get("mock_dir", "mock") / "mock_chunks.jsonl"
    else:
        path = ROOT / config.get("chunks_path", "data/chunks.jsonl")
    if not path.exists():
        raise RuntimeError(f"知识块文件不存在: {path}(provider={config.get('provider')})")
    index = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                chunk = json.loads(line)
                index[chunk["chunk_id"]] = chunk
    return index


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = _load_config()
    retriever, generator = get_providers(config)
    app.state.config = config
    app.state.retriever = retriever
    app.state.generator = generator
    app.state.chunk_index = _load_chunk_index(config)
    log_dir = ROOT / config.get("server", {}).get("log_dir", "logs")
    log_dir.mkdir(exist_ok=True)
    app.state.log_path = log_dir / "requests.jsonl"
    yield


app = FastAPI(title="西南财经大学教务智能问答系统", lifespan=lifespan)

# 仅本地开发用(file:// 直开前端调试);同源部署时无实际影响
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    return JSONResponse(status_code=500,
                        content={"error": "internal_error", "detail": str(exc)})


def _log_request(app: FastAPI, record: dict):
    record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(app.state.log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


@app.post("/ask")
async def ask(req: AskRequest, request: Request):
    config = request.app.state.config
    t0 = time.perf_counter()
    retrieved_chunks = request.app.state.retriever.retrieve(
        req.question, top_k=config.get("top_k", 5),
        college=req.college, cohort=req.cohort)
    t1 = time.perf_counter()

    # 拒答前置保险(计划书):top1 相似度低于 refuse_th 直接走拒答话术,不调生成
    refuse_th = config.get("refuse_th", 0.35)
    if not retrieved_chunks or retrieved_chunks[0]["score"] < refuse_th:
        result = {
            "answer_md": "现行文件中未找到明确规定,建议咨询教务处或学院教务办。\n\n"
                         "> 以下为知识库中与您的问题最相关的条款,供参考。",
            "citations": [], "refused": True,
        }
    else:
        result = request.app.state.generator.answer(
            req.question, retrieved_chunks,
            college=req.college, cohort=req.cohort)
    t2 = time.perf_counter()

    retrieved = [{"chunk_id": c["chunk_id"], "doc_title": c["doc_title"],
                  "article": c["article"], "college": c["college"],
                  "cohort": c["cohort"], "score": c["score"],
                  "snippet": c["text"][:SNIPPET_LEN]} for c in retrieved_chunks]
    response = {
        "answer_md": result["answer_md"],
        "citations": result["citations"],
        "retrieved": retrieved,
        "latency_ms": round((t2 - t0) * 1000, 1),
        "refused": result.get("refused", False),
    }
    _log_request(request.app, {
        "question": req.question, "college": req.college, "cohort": req.cohort,
        "retrieve_ms": round((t1 - t0) * 1000, 1),
        "generate_ms": round((t2 - t1) * 1000, 1),
        "latency_ms": response["latency_ms"], "refused": response["refused"],
        "top_chunk_ids": [c["chunk_id"] for c in retrieved_chunks],
        "top1_score": retrieved_chunks[0]["score"] if retrieved_chunks else None,
    })
    return response


@app.get("/source/{chunk_id}")
async def source(chunk_id: str, request: Request):
    chunk = request.app.state.chunk_index.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail=f"chunk_id 不存在: {chunk_id}")
    return chunk


@app.get("/meta")
async def meta(request: Request):
    chunks = request.app.state.chunk_index.values()
    colleges = sorted({c["college"] for c in chunks if c["level"] == "院级"})
    cohorts = sorted({c["cohort"] for c in chunks if c["cohort"] != "不限"})
    return {"colleges": colleges, "cohorts": cohorts}


@app.get("/")
async def home():
    return FileResponse(ROOT / "app" / "static" / "index.html")
