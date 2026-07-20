from app.demo_llm import DemoGeneralClient


def test_demo_general_client_answers_greetings_naturally() -> None:
    client = DemoGeneralClient()

    for greeting in ("你好", "您好！", "hello", "Hi"):
        answer = client.generate("system", f"【当前问题】\n{greeting}")

        assert answer == "你好！有什么我可以帮你的吗？"
        assert "你问的是" not in answer
