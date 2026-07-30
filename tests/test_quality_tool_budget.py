from knowledge.quality import ToolRunSnapshot


def test_skipped_tool_attempt_is_not_an_executed_tool():
    runs = [
        ToolRunSnapshot(
            tool_call_id="one",
            tool_name="collect_domain_evidence",
            agent_name="审批流专家",
            status="completed",
        ),
        ToolRunSnapshot(
            tool_call_id="two",
            tool_name="collect_domain_evidence",
            agent_name="审批流专家",
            status="skipped",
        ),
    ]

    executed = [item.tool_name for item in runs if item.status != "skipped"]

    assert executed == ["collect_domain_evidence"]
