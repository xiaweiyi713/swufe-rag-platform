from app.demo_llm import DemoGeneralClient


def test_demo_general_client_answers_greetings_naturally() -> None:
    client = DemoGeneralClient()

    for greeting in ("你好", "您好！", "hello", "Hi"):
        answer = client.generate("system", f"【当前问题】\n{greeting}")

        assert answer == "你好！有什么我可以帮你的吗？"
        assert "你问的是" not in answer


def test_demo_general_client_answers_gratitude_naturally() -> None:
    client = DemoGeneralClient()

    for gratitude in ("谢谢", "谢谢你！", "多谢", "感谢你"):
        answer = client.generate("system", f"【当前问题】\n{gratitude}")

        assert "不客气" in answer
        assert "你问的是" not in answer


def test_demo_general_client_introduces_itself_naturally() -> None:
    answer = DemoGeneralClient().generate(
        "system",
        "【当前问题】\n你好，请用一句话介绍你自己",
    )

    assert "西财教务问答助手" in answer
    assert "你问的是" not in answer


def test_demo_general_client_explains_rag_without_echoing() -> None:
    answer = DemoGeneralClient().generate("system", "【当前问题】\n请用一句话解释什么是RAG")

    assert "检索" in answer
    assert "证据" in answer
    assert "你问的是" not in answer


def test_demo_general_client_is_honest_for_unsupported_general_questions() -> None:
    answer = DemoGeneralClient().generate("system", "【当前问题】\n给我推荐一部电影")

    assert "未启用通用对话模型" in answer
    assert "你问的是" not in answer
