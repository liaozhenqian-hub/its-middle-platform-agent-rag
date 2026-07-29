from knowledge.agent_runtime.context import Citation
from knowledge.agent_runtime.evidence_policy import EvidenceLevel, EvidencePolicy


def test_product_document_removes_global_not_found_disclaimer():
    policy = EvidencePolicy()
    citations = [
        Citation(
            source_type="product_document",
            source_id="metric-definition",
            title="指标类型定义",
            domain="指标平台",
            metadata={},
        )
    ]
    answer = f"{policy._SAFEGUARD}\n\n以下内容仅作为待验证线索：已确认指标定义。"

    safeguarded = policy.safeguard(answer, citations)

    assert policy.classify(citations) == EvidenceLevel.CONFIRMED
    assert not safeguarded.startswith(policy._SAFEGUARD)
