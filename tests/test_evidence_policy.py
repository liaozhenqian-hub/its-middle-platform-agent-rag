from knowledge.agent_runtime.context import Citation
from knowledge.agent_runtime.evidence_policy import EvidenceLevel, EvidencePolicy


def test_no_citations_are_not_found_and_negative_claim_is_downgraded():
    policy = EvidencePolicy()

    answer = policy.safeguard("系统不支持子工作流。", [])

    assert policy.classify([]) == EvidenceLevel.NOT_FOUND
    assert answer.startswith("本次检索暂未找到该能力的明确实现")
    assert "待验证线索" in answer


def test_dto_field_citation_cannot_confirm_negative_capability_claim():
    policy = EvidencePolicy()
    citations = [
        Citation(
            source_type="code",
            source_id="dto-field",
            title="WorkflowConfig.retryCount",
            domain="工作流",
            metadata={"symbol_kind": "field", "symbol_name": "retryCount"},
        )
    ]

    assert policy.classify(citations) == EvidenceLevel.INFERRED
    answer = policy.safeguard("工作流没有并行汇聚能力。", citations)
    assert answer.startswith("本次检索暂未找到该能力的明确实现")


def test_explicit_product_limitation_can_confirm_negative_claim():
    policy = EvidencePolicy()
    citations = [
        Citation(
            source_type="product_document",
            source_id="workflow-limitations",
            title="工作流限制",
            domain="工作流",
            metadata={"explicit_limitation": True},
        )
    ]

    answer = "已确认：当前版本不支持子工作流。"

    assert policy.classify(citations) == EvidenceLevel.CONFIRMED
    assert policy.safeguard(answer, citations) == answer


def test_code_method_is_confirmed_evidence_for_positive_conclusion():
    policy = EvidencePolicy()
    citations = [
        Citation(
            source_type="code",
            source_id="method-1",
            title="RetryExecutor.execute",
            domain="工作流",
            metadata={"symbol_kind": "method", "symbol_name": "execute"},
        )
    ]

    assert policy.classify(citations) == EvidenceLevel.CONFIRMED
    assert policy.safeguard("已确认：该节点会执行重试。", citations) == (
        "已确认：该节点会执行重试。"
    )


def test_real_code_symbol_type_confirms_retrieved_implementation():
    policy = EvidencePolicy()
    citations = [
        Citation(
            source_type="code",
            source_id="admin-transfer-method",
            title="ProcessTaskController.adminTransferTask",
            domain="审批流",
            metadata={
                "symbol_type": "method",
                "symbol_name": "adminTransferTask",
            },
        )
    ]

    answer = "已确认：管理员转办接口已经实现。"

    assert policy.classify(citations) == EvidenceLevel.CONFIRMED
    assert policy.safeguard(answer, citations) == answer


def test_deployment_uncertainty_does_not_downgrade_confirmed_code_answer():
    policy = EvidencePolicy()
    answer = (
        "代码中已有管理员转办实现，但未检索到 Swagger 定义，"
        "无法确认是否已部署到开发环境。"
    )

    assert policy.safeguard(answer, []) == answer


def test_confirmed_code_removes_contradictory_global_not_found_disclaimer():
    policy = EvidencePolicy()
    citations = [
        Citation(
            source_type="code",
            source_id="admin-transfer-method",
            title="ProcessTaskController.adminTransferTask",
            domain="审批流",
            metadata={"symbol_type": "method", "symbol_name": "adminTransferTask"},
        )
    ]
    answer = (
        "本次检索暂未找到该能力的明确实现，不能据此确认系统不支持。\n\n"
        "以下内容仅作为待验证线索：代码中已有管理员转办实现，"
        "但未检索到 Swagger，无法确认是否已部署到开发环境。"
    )

    safeguarded = policy.safeguard(answer, citations)

    assert safeguarded.startswith("代码中已有管理员转办实现")
    assert "未检索到 Swagger" in safeguarded
    assert "无法确认是否已部署" in safeguarded
    assert "仅作为待验证线索" not in safeguarded
