from swufe_rag.routing.router import HybridRouter


def test_version_suffix_is_recognized_as_policy_year() -> None:
    decision = HybridRouter().route("2024年版推免综合成绩怎么计算？")
    assert decision.mode == "school_rag"
    assert decision.policy_year == 2024
