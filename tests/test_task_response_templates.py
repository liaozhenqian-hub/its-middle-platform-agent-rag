from knowledge.agent_runtime.agent_factory import TASK_RESPONSE_TEMPLATES
from knowledge.agent_runtime.evidence_policy import EvidencePolicy


def test_task_templates_require_named_sections_and_partial_evidence_notice():
    for task_type in ("api_contract", "how_to", "requirement_analysis", "code_lookup"):
        template = TASK_RESPONSE_TEMPLATES[task_type]
        assert "结论" in template
        assert "证据" in template
        assert "未确认" in template
    assert "没有证据" in EvidencePolicy.MISSING_FACT_NOTICE
