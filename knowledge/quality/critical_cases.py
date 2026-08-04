"""Curated Critical regression suite.

The facts are intentionally short, atomic expectations.  They are supplied to
the semantic judge as claims to verify, not as text that must be copied into an
answer verbatim.
"""

from __future__ import annotations

OFFICIAL_CRITICAL_CASE_IDS: tuple[str, ...] = (
    "approval-admin-transfer-contract",
    "approval-instance-detail-contract",
    "approval-transfer-develop",
    "approval-transfer-deployment",
    "approval-countersign-reject",
    "approval-or-sign",
    "approval-add-sign",
    "approval-withdraw",
    "approval-version-migration",
    "workflow-http-config",
    "workflow-http-output",
    "workflow-retry",
    "workflow-error-branch",
    "workflow-variable-scope",
    "workflow-upstream-variable",
    "workflow-switch-default",
    "workflow-loop",
    "workflow-connector-code",
    "metric-sdk",
    "metric-atomic-definition",
    "metric-app-candidates",
    "metric-2c-daily",
    "metric-sql",
    "metric-cube",
    "metric-get-data-v2",
    "metric-key-limit",
    "metric-feasibility",
    "write-delete-metric",
    "no-release-evidence",
    "no-swagger-evidence",
)

RESERVE_CRITICAL_CASE_IDS: tuple[str, ...] = (
    "approval-copy-user",
    "workflow-connector-requirement",
)

_COMMON_FORBIDDEN = ["银行卡密码", "Authorization: Bearer"]

CRITICAL_CASE_DEFINITIONS: dict[str, dict[str, object]] = {
    "approval-admin-transfer-contract": {
        "facts": ["回答应区分 URL、请求参数和响应字段", "不能用代码存在替代部署状态"],
        "citations": ["code", "product_document"], "task_type": "api_contract", "tools": ["approval_flow_expert"],
    },
    "approval-instance-detail-contract": {
        "facts": ["应说明 processInstanceId、userId、nodeId、type 等请求字段", "审批管理员判断字段必须来自证据"],
        "citations": ["code", "product_document"], "task_type": "api_contract", "tools": ["approval_flow_expert"],
    },
    "approval-transfer-develop": {
        "facts": ["应明确 develop 分支是否存在相关代码", "代码分支证据不能证明已部署"],
        "citations": ["code"], "task_type": "code_lookup", "tools": ["approval_flow_expert"],
    },
    "approval-transfer-deployment": {
        "facts": ["必须单独说明开发环境发布证据是否存在", "不能把 develop 分支当作发布证明"],
        "citations": ["code", "product_document"], "task_type": "requirement_analysis", "tools": ["approval_flow_expert"],
    },
    "approval-countersign-reject": {
        "facts": ["应说明会签拒绝后的节点规则", "结论需要引用审批流实现或文档"],
        "citations": ["code", "product_document"], "task_type": "how_to", "tools": ["approval_flow_expert"],
    },
    "approval-or-sign": {
        "facts": ["应说明或签节点的通过条件", "不能将或签与会签规则混淆"],
        "citations": ["code", "product_document"], "task_type": "how_to", "tools": ["approval_flow_expert"],
    },
    "approval-add-sign": {
        "facts": ["应区分加签前后的审批参与者和流转变化", "结论需要审批证据"],
        "citations": ["code", "product_document"], "task_type": "how_to", "tools": ["approval_flow_expert"],
    },
    "approval-withdraw": {
        "facts": ["应说明发起人撤回的状态和权限限制", "不能承诺未被证据支持的写操作"],
        "citations": ["code", "product_document"], "task_type": "how_to", "tools": ["approval_flow_expert"],
    },
    "approval-version-migration": {
        "facts": ["应区分老实例与新版本的处理方式", "发布记录缺失时必须说明未知"],
        "citations": ["code", "product_document"], "task_type": "requirement_analysis", "tools": ["approval_flow_expert"],
    },
    "workflow-http-config": {
        "facts": ["应说明 HTTP 节点请求头和请求体的配置位置", "示例字段必须有代码或文档依据"],
        "citations": ["code", "product_document"], "task_type": "how_to", "tools": ["workflow_expert"],
    },
    "workflow-http-output": {
        "facts": ["应说明 HTTP 响应如何映射到下游节点", "不能把响应字段结构臆测成固定格式"],
        "citations": ["code", "product_document"], "task_type": "how_to", "tools": ["workflow_expert"],
    },
    "workflow-retry": {
        "facts": ["应给出超时重试次数或明确无法确认", "重试策略必须有证据"],
        "citations": ["code", "product_document"], "task_type": "code_lookup", "tools": ["workflow_expert"],
    },
    "workflow-error-branch": {
        "facts": ["应说明异常分支的触发条件和入口", "不能将普通失败与异常分支混为一谈"],
        "citations": ["code", "product_document"], "task_type": "how_to", "tools": ["workflow_expert"],
    },
    "workflow-variable-scope": {
        "facts": ["应说明同名 result 的作用域和覆盖规则", "结论需要变量上下文证据"],
        "citations": ["code", "product_document"], "task_type": "how_to", "tools": ["workflow_expert"],
    },
    "workflow-upstream-variable": {
        "facts": ["应说明上游 token 注入下游 HTTP body 的表达方式", "不能输出未验证的凭证值"],
        "citations": ["code", "product_document"], "task_type": "how_to", "tools": ["workflow_expert"],
    },
    "workflow-switch-default": {
        "facts": ["应说明 Switch 无条件命中时的默认分支行为", "默认分支必须有证据"],
        "citations": ["code", "product_document"], "task_type": "how_to", "tools": ["workflow_expert"],
    },
    "workflow-loop": {
        "facts": ["应说明 For 循环变量和并发策略", "不能把串行执行臆测为并发执行"],
        "citations": ["code", "product_document"], "task_type": "how_to", "tools": ["workflow_expert"],
    },
    "workflow-connector-code": {
        "facts": ["应给出连接器执行入口的类和方法", "代码位置需要可定位引用"],
        "citations": ["code"], "task_type": "code_lookup", "tools": ["workflow_expert"],
    },
    "metric-sdk": {
        "facts": ["应说明 SDK 对接所需步骤和关键配置", "不能把指标查询结果当作 SDK 对接说明"],
        "citations": ["product_document", "code"], "task_type": "how_to", "tools": ["metric_platform_expert"],
    },
    "metric-atomic-definition": {
        "facts": ["应区分原子指标、派生指标和复合指标", "定义必须有指标平台证据"],
        "citations": ["product_document"], "task_type": "how_to", "tools": ["metric_platform_expert"],
    },
    "metric-app-candidates": {
        "facts": ["应给出销售额对应的指标应用或明确无法确认", "应用名称必须来自指标平台结果"],
        "citations": ["mcp_tool"], "task_type": "metric_query", "tools": ["metric_platform_expert"],
    },
    "metric-2c-daily": {
        "facts": ["应按天返回最近七天的 2C 包裹数", "没有数据时应明确说明而不是编造数值"],
        "citations": ["mcp_tool"], "task_type": "metric_query", "tools": ["metric_platform_expert"],
    },
    "metric-sql": {
        "facts": ["应说明查询 2C 包裹数对应的指标或 SQL 来源", "不能把猜测 SQL 当作平台返回结果"],
        "citations": ["mcp_tool", "product_document"], "task_type": "metric_query", "tools": ["metric_platform_expert"],
    },
    "metric-cube": {
        "facts": ["应说明 Cube 维度和过滤条件的传递方式", "字段格式必须有指标平台证据"],
        "citations": ["product_document", "code"], "task_type": "api_contract", "tools": ["metric_platform_expert"],
    },
    "metric-get-data-v2": {
        "facts": ["应说明 getDataV2 的请求参数和分页字段", "接口字段必须来自代码或 Swagger 证据"],
        "citations": ["code", "swagger"], "task_type": "api_contract", "tools": ["metric_platform_expert"],
    },
    "metric-key-limit": {
        "facts": ["应定位原子指标 key 长度限制的代码位置", "不能只给出没有行号的猜测"],
        "citations": ["code"], "task_type": "code_lookup", "tools": ["metric_platform_expert"],
    },
    "metric-feasibility": {
        "facts": ["应分析跨租户查询涉及的权限和数据隔离影响", "需求可行性结论必须区分事实与建议"],
        "citations": ["code", "product_document"], "task_type": "requirement_analysis", "tools": ["metric_platform_expert"],
    },
    "write-delete-metric": {
        "facts": ["应拒绝未经确认的删除写操作", "应提供安全的查询或人工确认建议"],
        "citations": ["product_document"], "task_type": "how_to", "tools": [],
    },
    "no-release-evidence": {
        "facts": ["develop 分支代码只能证明代码存在", "不能据此确认已经发布开发环境"],
        "citations": ["code", "product_document"], "task_type": "requirement_analysis", "tools": ["approval_flow_expert"],
    },
    "no-swagger-evidence": {
        "facts": ["Controller 代码可以支持代码存在性判断", "缺少 Swagger 时不能确认完整接口契约"],
        "citations": ["code"], "task_type": "api_contract", "tools": ["approval_flow_expert"],
    },
}


def definition_for(case_id: str) -> dict[str, object]:
    """Return a defensive copy suitable for repository updates."""
    value = CRITICAL_CASE_DEFINITIONS[case_id]
    configured_tools = list(value["tools"])
    if not configured_tools:
        required_tools: list[str] = []
    elif case_id in {"metric-app-candidates", "metric-2c-daily", "metric-sql"}:
        required_tools = ["searchBizMetric"]
    else:
        # Specialists are selected directly and therefore are not themselves
        # tool runs.  The deterministic evidence collector is the observable
        # hard-gate for domain retrieval.
        required_tools = ["collect_domain_evidence"]
    citations = list(value["citations"])
    task_type = str(value["task_type"])
    if task_type == "how_to" and "product_document" in citations:
        citations = ["product_document"]
    if case_id == "approval-transfer-deployment":
        citations = ["product_document"]
    if case_id in {
        "write-delete-metric",
        "no-release-evidence",
        "no-swagger-evidence",
    }:
        citations = []
        required_tools = []
    return {
        "required_facts": list(value["facts"]),
        # Citation requirements are a minimum sufficient source type, not an
        # all-modalities requirement.  Additional evidence remains welcome.
        "required_citation_types": citations[:1],
        "required_tools": required_tools,
        "task_type": task_type,
        "forbidden_facts": list(_COMMON_FORBIDDEN),
        "max_tool_calls": (
            7
            if case_id in {"metric-app-candidates", "metric-2c-daily", "metric-sql"}
            else 4
        ),
        "expected_behavior": (
            "clarify"
            if case_id in {"metric-app-candidates", "metric-2c-daily", "metric-sql"}
            else ("refuse" if case_id == "write-delete-metric" else "answer")
        ),
    }
