import pytest

from knowledge.agent_runtime.intent_router import DomainIntentRouter


def test_router_recognizes_workflow_colloquial_intents():
    router = DomainIntentRouter()

    assert router.route("HTTP 节点超时了会重试几次").domains == ("workflow",)
    assert router.route("两个节点都叫 result，后面的会覆盖吗").domains == ("workflow",)
    assert router.route("所有条件都不满足会走哪条默认分支").domains == ("workflow",)
    assert router.route("连接器返回字符串，规则节点按数字比较").domains == ("workflow",)


def test_router_recognizes_metric_and_approval_intents():
    router = DomainIntentRouter()

    assert router.route("从零建一个能查数据的指标").domains == ("metric-platform",)
    assert router.route("会签有一个人拒绝其他人还要批吗").domains == (
        "approval-flow",
    )
    assert router.route("帮我看近七天每天 2C 发货包裹数").domains == (
        "metric-platform",
    )
    assert router.route("通过 MCP 接口调用会有日志记录在数据库吗").domains == (
        "metric-platform",
    )


def test_router_treats_metric_platform_followup_as_onboarding_not_metric_query():
    decision = DomainIntentRouter().route("那我如何对接这个指标平台呢")

    assert decision.domains == ("metric-platform",)
    assert decision.task_type == "how_to"


def test_router_keeps_vague_and_cross_domain_questions_unforced():
    router = DomainIntentRouter()

    vague = router.route("这个审批不对")
    cross = router.route("审批通过后触发工作流连接器")

    assert vague.domains == ("approval-flow",)
    assert vague.needs_clarification is True
    assert cross.domains == ("approval-flow", "workflow")


def test_router_requires_real_error_signal_for_bug_route():
    router = DomainIntentRouter()

    assert router.route("连接器输出类型转换").intent != "bug"
    bug = router.route("生产环境接口报错 500，traceId=abc-123")
    assert bug.intent == "bug"
    assert bug.domains == ("bug",)


def test_router_prefers_approval_contract_lookup_over_incidental_error_payload():
    message = (
        '/api/flow/task/adminTransferTask SDK调用报错，返回500，'
        '"traceId":"6de09eb4-9669-4d72-9061-bb42f18f43a0"，'
        '帮我看看审批流开发环境是否有这个接口'
    )

    decision = DomainIntentRouter().route(message)

    assert decision.domains == ("approval-flow",)
    assert decision.intent == "approval-flow"
    assert decision.task_type == "api_contract"
    assert decision.needs_clarification is False


def test_router_keeps_explicit_root_cause_request_in_bug_graph():
    decision = DomainIntentRouter().route(
        '审批流接口报错500，traceId=6de09eb4-9669-4d72-9061-bb42f18f43a0，帮我查日志定位根因'
    )

    assert decision.domains == ("bug",)


def test_router_keeps_product_capability_questions_out_of_bug_graph():
    router = DomainIntentRouter()

    assert router.route("工作流节点异常时如何配置重试或异常分支").domains == (
        "workflow",
    )
    assert router.route("原子指标 key 太长报错，代码限制多少").domains == (
        "metric-platform",
    )
    assert router.route("接口返回字符串 10，后面规则按数字判断能自动转吗").domains == (
        "workflow",
    )
    assert router.route("接口回的是字符串 10，后面按数字判断能自动转吗").domains == (
        "workflow",
    )


def test_router_recognizes_node_parameter_passing_and_approval_versioning():
    router = DomainIntentRouter()

    assert router.route("上一个节点返回的 token 怎么放进下一个 HTTP 请求 body").domains == (
        "workflow",
    )
    assert router.route("流程改版发布后老单子能迁移吗").domains == (
        "approval-flow",
    )


@pytest.mark.parametrize(
    ("question", "task_type"),
    [
        ("审批流管理员转办接口的入出参是什么", "api_contract"),
        ("工作流 HTTP 节点怎么配置", "how_to"),
        ("查一下 ProcessTaskController 代码在哪里", "code_lookup"),
        ("这个审批需求是否合理，影响哪些模块", "requirement_analysis"),
        ("帮我查每天发货 2C 包裹数", "metric_query"),
        ("开发环境报错 traceId abc-123", "bug"),
    ],
)
def test_router_classifies_domain_task_type(question, task_type):
    assert DomainIntentRouter().route(question).task_type == task_type
