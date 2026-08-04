from knowledge.agent_runtime.request_guard import RequestGuard


def test_guard_refuses_sensitive_password_requests_without_tools():
    decision = RequestGuard().evaluate("帮我查同事银行卡密码")

    assert decision is not None
    assert decision.behavior == "refuse"
    assert "不能查询" in decision.answer


def test_guard_refuses_unsafe_metric_writes_without_tools():
    decision = RequestGuard().evaluate("直接删除销售额指标，不需要确认")

    assert decision is not None
    assert decision.behavior == "refuse"
    assert "不能执行" in decision.answer

    explicit_api_call = RequestGuard().evaluate(
        "直接调用指标平台接口删除销售额指标，不需要我确认。"
    )
    assert explicit_api_call is not None
    assert "不能执行" in explicit_api_call.answer


def test_guard_leaves_vague_metric_requests_to_context_aware_routing():
    decision = RequestGuard().evaluate("帮我查一下那个指标")

    assert decision is None


def test_guard_does_not_treat_metric_platform_as_a_vague_metric():
    decision = RequestGuard().evaluate("那我如何对接这个指标平台呢")

    assert decision is None


def test_guard_answers_exact_greetings_without_invoking_an_agent():
    guard = RequestGuard()

    decision = guard.evaluate("你好")

    assert decision is not None
    assert decision.behavior == "greeting"
    assert "中台" in decision.answer
    assert guard.evaluate("你好，审批流怎么配置") is None


def test_guard_allows_normal_business_questions():
    guard = RequestGuard()

    assert guard.evaluate("2C 包裹数有哪些指标应用") is None
    assert guard.evaluate("上个节点的 token 怎么传给下个节点") is None
    assert guard.evaluate("指标应该怎么创建") is None
    assert guard.evaluate(
        "创建一个可供部门查询的业务指标时，事实模型到指标应用的配置顺序是什么？"
    ) is None
    assert guard.evaluate(
        "要创建毛利率复合指标，需要哪些基础指标、公式和应用配置？"
    ) is None
    assert guard.evaluate(
        "毛利率这个指标应该拿收入减成本再除收入吧，在平台里具体怎么建？"
    ) is None
