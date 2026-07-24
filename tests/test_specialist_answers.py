import json
from types import SimpleNamespace

import pytest

from knowledge.agent_runtime.context import AgentRunContext, Citation
from knowledge.agent_runtime.specialist_answers import create_specialist_output_extractor


@pytest.mark.asyncio
async def test_specialist_tool_output_is_structured_and_contains_only_public_evidence():
    context = AgentRunContext("conversation-1", "run-1")
    context.citations.append(Citation(
        source_type="code",
        source_id="chunk-1",
        title="ProcessController.getDetail",
        domain="审批流",
        metadata={"token": "must-not-leak"},
    ))
    result = SimpleNamespace(
        final_output="已确认代码中存在该接口，但无法确认是否已部署。",
        context_wrapper=SimpleNamespace(context=context),
    )

    payload = json.loads(
        await create_specialist_output_extractor("审批流专家")(result)
    )

    assert payload["specialist"] == "审批流专家"
    assert payload["conclusion"].startswith("已确认")
    assert payload["evidence"] == [{
        "source_type": "code",
        "source_id": "chunk-1",
        "title": "ProcessController.getDetail",
        "domain": "审批流",
    }]
    assert payload["unknowns"] == ["但无法确认是否已部署"]
    assert payload["deployment_status"] == "unknown"
    assert "must-not-leak" not in json.dumps(payload, ensure_ascii=False)


def test_specialist_instructions_require_direct_evidence_bound_sections():
    from knowledge.agent_runtime.agent_factory import AgentFactory

    class Registry:
        def get(self, app_id, domain):
            raise AssertionError("agent construction must not retrieve")

    topology = AgentFactory(model="fake-model", registry=Registry()).create()

    for specialist in topology.specialists.values():
        assert "先给直接结论" in specialist.instructions
        assert "证据" in specialist.instructions
        assert "未确认事项" in specialist.instructions
    assert "不要改写为整体无法确认" in topology.manager.instructions
