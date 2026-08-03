from knowledge.agent_runtime.agent_factory import AgentFactory
import knowledge.agent_runtime.agent_factory as agent_factory_module


class FakeRegistry:
    def get(self, app_id, domain):
        raise AssertionError("building agents must not execute retrieval")


class FakeMCP:
    pass


def test_composite_evidence_receives_the_configured_four_call_budget(monkeypatch):
    captured = {}

    def create_tool(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(agent_factory_module, "create_domain_evidence_tool", create_tool)
    factory = AgentFactory(
        model="fake-model",
        registry=FakeRegistry(),
        retrieval_max_calls=4,
    )

    factory._specialist_tools(None, "approval-flow", "Approval flow", "Expert")

    assert captured["max_calls"] == 4


def test_agent_factory_builds_manager_with_domain_and_bug_specialists_as_tools():
    model = "fake-model"
    topology = AgentFactory(
        model=model,
        registry=FakeRegistry(),
        metric_mcp_server=FakeMCP(),
        bug_graph_service=object(),
    ).create()

    assert topology.manager.name == "Manager Agent"
    assert "trace ID" in topology.manager.instructions
    assert "bug_diagnosis_expert" in topology.manager.instructions
    assert set(topology.specialists) == {
        "metric_platform_expert",
        "approval_flow_expert",
        "workflow_expert",
    }
    assert {tool.name for tool in topology.manager.tools} == {
        *topology.specialists,
        "bug_diagnosis_expert",
    }
    assert topology.manager.model_settings.parallel_tool_calls is False
    for boundary in (
        "中台知识问答",
        "需求可行性分析",
        "只读指标查询",
        "问候最多用一句话",
        "不展开闲聊",
        "明确与中台无关时，不调用任何工具",
        "我只能协助以下中台业务内容",
        "审批流、工作流、指标平台的对接与使用",
        "产品文档、代码、Swagger",
        "需求澄清、可行性与影响分析",
        "Bug、报错、异常与 trace 定位",
        "个人隐私、银行卡密码、账号口令",
        "只追问一个澄清问题",
        "不得执行写操作",
        "不得绕过权限",
        "没有证据时不得确认内部事实",
        "不得泄露凭证",
    ):
        assert boundary in topology.manager.instructions
    for tool_name, specialist in topology.specialists.items():
        if tool_name == "bug_diagnosis_expert":
            continue
        assert specialist.model_settings.tool_choice == "required"
        assert specialist.model_settings.parallel_tool_calls is False

    metric = topology.specialists["metric_platform_expert"]
    assert metric.mcp_servers == [topology.metric_mcp_server]
    assert {tool.name for tool in metric.tools} == {
        "collect_domain_evidence",
        "prepare_metric_query",
        "query_metric_data_guarded",
        "query_metric_sql_guarded",
    }
    approval = topology.specialists["approval_flow_expert"]
    workflow = topology.specialists["workflow_expert"]
    assert {tool.name for tool in approval.tools} == {
        "collect_domain_evidence",
    }
    assert {tool.name for tool in workflow.tools} == {
        "collect_domain_evidence",
    }


def test_agent_factory_omits_mcp_when_unavailable_but_keeps_metric_rag():
    topology = AgentFactory(model="fake-model", registry=FakeRegistry()).create()

    metric = topology.specialists["metric_platform_expert"]
    assert metric.mcp_servers == []
    assert {tool.name for tool in metric.tools} == {
        "collect_domain_evidence",
    }
    assert "bug_diagnosis_expert" not in {tool.name for tool in topology.manager.tools}


def test_manager_specialist_tools_are_limited_by_selected_domain():
    topology = AgentFactory(
        model="fake-model",
        registry=FakeRegistry(),
        bug_graph_service=object(),
    ).create()
    tools = {tool.name: tool for tool in topology.manager.tools}
    from agents import RunContextWrapper
    from knowledge.agent_runtime.context import AgentRunContext

    metric_context = RunContextWrapper(
        AgentRunContext("conversation-1", "run-1", domain_id="metric-platform")
    )
    root_context = RunContextWrapper(AgentRunContext("conversation-2", "run-2"))

    assert tools["metric_platform_expert"].is_enabled(metric_context, topology.manager) is True
    assert tools["approval_flow_expert"].is_enabled(metric_context, topology.manager) is False
    assert tools["approval_flow_expert"].is_enabled(root_context, topology.manager) is True
    assert tools["bug_diagnosis_expert"].is_enabled is True


def test_manager_can_use_server_scoped_memory_tools():
    topology = AgentFactory(
        model="fake-model",
        registry=FakeRegistry(),
        memory_service=object(),
    ).create()

    assert {tool.name for tool in topology.manager.tools} >= {
        "search_user_memory",
        "search_domain_memory",
    }


def test_manager_can_use_server_scoped_entity_memory_tool():
    topology = AgentFactory(
        model="fake-model",
        registry=FakeRegistry(),
        entity_memory_repository=object(),
    ).create()

    assert "search_entity_memory" in {tool.name for tool in topology.manager.tools}


def test_high_confidence_domain_routes_point_directly_to_specialists():
    topology = AgentFactory(model="fake-model", registry=FakeRegistry()).create()

    assert topology.domain_managers["approval-flow"] is topology.specialists[
        "approval_flow_expert"
    ]
    assert topology.domain_managers["workflow"] is topology.specialists[
        "workflow_expert"
    ]
    assert topology.domain_managers["metric-platform"] is topology.specialists[
        "metric_platform_expert"
    ]
