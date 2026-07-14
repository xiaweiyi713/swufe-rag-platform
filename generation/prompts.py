"""Trust-first prompts and deterministic context assembly."""

from __future__ import annotations

from contracts import RetrievedChunk


REFUSAL_TEXT = "现行文件中未找到明确规定，建议咨询教务处或学院教务办。"

SYSTEM_PROMPT = f"""你是西南财经大学教务政策问答助手。
回答规则：
1. 只能依据用户消息中的【参考资料】回答，禁止使用资料外知识或模型记忆；
2. 每个政策事实或结论句末必须标注资料编号，例如[1]或[2][3]；
3. 资料不足时只回答“{REFUSAL_TEXT}”，不得猜测；
4. 学分、绩点、比例、年份、课程代码等必须与资料原文完全一致；
5. 不得把“仅供参考”的相关条款写成确定结论；
6. 只输出 Markdown 回答正文，不输出 JSON、参考资料列表或分析过程。"""


def format_context(chunks: list[RetrievedChunk]) -> str:
    sections: list[str] = []
    for marker, chunk in enumerate(chunks, start=1):
        scope = f"{chunk['college']} / {chunk['cohort']} / {chunk['status']}"
        sections.append(
            f"[{marker}]《{chunk['doc_title']}》{chunk['article']}\n"
            f"适用范围：{scope}\n"
            f"原文：{chunk['text']}"
        )
    return "\n\n".join(sections)


def build_user_prompt(query: str, chunks: list[RetrievedChunk]) -> str:
    return f"【参考资料】\n{format_context(chunks)}\n\n【问题】\n{query.strip()}"


def build_repair_prompt(
    query: str,
    chunks: list[RetrievedChunk],
    invalid_answer: str,
    validation_error: str,
) -> str:
    return (
        f"【参考资料】\n{format_context(chunks)}\n\n"
        f"【原问题】\n{query.strip()}\n\n"
        f"【未通过校验的回答】\n{invalid_answer}\n\n"
        f"【校验错误】\n{validation_error}\n\n"
        "请只修复引用或删除无依据内容，不得新增事实。"
        f"若无法修复，只回答“{REFUSAL_TEXT}”"
    )

