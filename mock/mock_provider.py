"""模块D桩实现:MockRetriever / MockGenerator。

MockRetriever 的过滤逻辑与契约2完全一致(这部分是真实逻辑,可用于验证过滤行为),
仅排序打分用简易字符相似度代替向量检索;
MockGenerator 用关键词路由返回 mock_answers.json 里的预制答案,无匹配则走拒答模板。
"""
import copy
import json
from pathlib import Path

from app.providers import Generator, Retriever

ROOT = Path(__file__).resolve().parent.parent  # 仓库根,使 mock_dir 不依赖启动目录

# 口语词 -> 文件用语的查询扩展,仅用于弥补字符相似度不懂语义的缺陷
_SYNONYMS = {
    "保研": "推荐免试 推免",
    "挂科": "不及格",
    "挂过": "不及格",
    "挂了": "不及格",
    "GPA": "绩点 平均学分绩点",
    "gpa": "绩点 平均学分绩点",
    "六级": "英语六级 外语 CET",
    "转系": "转专业",
    "毕不了业": "毕业 学分",
    "权重": "综合成绩 构成",
}


def _expand(query: str) -> str:
    extra = [v for k, v in _SYNONYMS.items() if k in query]
    return query + " " + " ".join(extra) if extra else query


def _bigrams(s: str) -> set:
    s = "".join(ch for ch in s if not ch.isspace())
    return {s[i:i + 2] for i in range(len(s) - 1)}


class MockRetriever(Retriever):

    def __init__(self, config: dict):
        mock_dir = ROOT / config.get("mock_dir", "mock")
        self.chunks = [json.loads(line)
                       for line in (mock_dir / "mock_chunks.jsonl").read_text(encoding="utf-8").splitlines()
                       if line.strip()]

    def retrieve(self, query: str, top_k: int = 5,
                 college: str = None, cohort: str = None) -> list:
        # 契约2:过滤必须在排序前
        candidates = [c for c in self.chunks if self._pass_filter(c, college, cohort)]
        q = _bigrams(_expand(query))
        scored = []
        for c in candidates:
            hay = _bigrams(c["text"]) | _bigrams(c["doc_title"]) | _bigrams(c["article"])
            overlap = len(q & hay) / max(1, len(q))  # 查询bigram被覆盖比例, 0~1
            # 开方拉伸,使分数分布接近真实向量余弦相似度的量级,
            # 与 config 的 refuse_th(为真实检索设计)保持语义兼容:
            # 相关问题 >0.5,库外问题 ~0,阈值 0.35 两侧分界清晰
            score = overlap ** 0.5
            scored.append(({**c, "score": round(score, 4)}))
        scored.sort(key=lambda c: c["score"], reverse=True)
        return scored[:top_k]

    @staticmethod
    def _pass_filter(chunk: dict, college: str, cohort: str) -> bool:
        if chunk["status"] != "现行":
            return False
        if college and chunk["college"] not in ("全校", "校级", college):
            return False
        if cohort and chunk["cohort"] not in ("不限", cohort):
            return False
        return True


class MockGenerator(Generator):

    def __init__(self, config: dict):
        mock_dir = ROOT / config.get("mock_dir", "mock")
        data = json.loads((mock_dir / "mock_answers.json").read_text(encoding="utf-8"))
        self.entries = data["answers"]
        self.refusal = data["refusal"]

    def answer(self, query: str, chunks: list, college: str = None, cohort: str = None) -> dict:
        """契约3签名 + 可选身份参数(server 额外传入;真实模块C见主 README 待对齐项 D-7)。

        未传身份时从 chunks 元数据推断:检索结果已按范围过滤,
        其中院级块的 college 即用户学院,非"不限"的 cohort 即用户年级。
        """
        if college is None:
            college = next((c["college"] for c in chunks if c.get("level") == "院级"), None)
        if cohort is None:
            cohort = next((c["cohort"] for c in chunks if c.get("cohort") not in (None, "不限")), None)

        best, best_score = None, 0.0
        for e in self.entries:
            if e["college"] and e["college"] != college:
                continue
            if e["cohort"] and e["cohort"] != cohort:
                continue
            hits1 = sum(1 for k in e["keywords"] if k in query)
            hits2 = sum(1 for k in e["keywords2"] if k in query)
            if hits1 < e["min_hits"]:
                continue
            if e["keywords2"] and hits2 == 0:
                continue
            # 特异性加分:限定了学院/年级的组优先于通用组
            score = hits1 + hits2 + (0.5 if e["college"] else 0) + (0.5 if e["cohort"] else 0)
            if score > best_score:
                best, best_score = e, score

        if best is None:
            return copy.deepcopy(self.refusal)
        return {"answer_md": best["answer_md"],
                "citations": copy.deepcopy(best["citations"]),
                "refused": best["refused"]}
