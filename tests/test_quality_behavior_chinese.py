from knowledge.quality.behavior import BehaviorChecker


def test_clarification_recognizes_confirmation_wording():
    answer = "需要您确认是否选择该指标应用，确认后我可以继续查询数据。"

    assert BehaviorChecker.matches("clarify", answer, ["searchMetricApp"])
