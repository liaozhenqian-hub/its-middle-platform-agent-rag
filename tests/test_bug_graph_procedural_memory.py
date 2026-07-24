from types import SimpleNamespace

import pytest

from knowledge.bug_graph.service import BugDiagnosisGraphService


class ProcedureService:
    def __init__(self):
        self.calls = []

    async def recall_procedures(self, **kwargs):
        self.calls.append(kwargs)
        spec = SimpleNamespace(
            procedure_version=2,
            allowed_tools=("query_trace_logs", "search_branch_code"),
        )
        return [(SimpleNamespace(id="procedure-1"), spec)]


@pytest.mark.asyncio
async def test_bug_graph_selects_procedure_with_server_controlled_scope_in_observe_mode():
    procedures = ProcedureService()
    service = object.__new__(BugDiagnosisGraphService)
    service.procedural_memory_service = procedures
    service.procedural_guidance_enabled = True
    service.procedural_observe_only = True
    service.procedural_recall_limit = 3
    service._run_users = {"run-1": "user-1"}

    result = await service._select_procedure({
        "run_id": "run-1", "environment": "prod",
        "domain_hints": ["approval-flow"],
    })

    assert procedures.calls == [{
        "user_id": "user-1", "domain_id": "approval-flow",
        "task_type": "bug_diagnosis", "environment": "prod",
        "branch": "master", "limit": 3,
    }]
    assert result == {
        "selected_procedure_id": "procedure-1",
        "selected_procedure_version": 2,
        "procedure_capabilities": ["query_trace_logs", "search_branch_code"],
        "procedure_observe_only": True,
        "current_stage": "select_procedure",
    }


@pytest.mark.asyncio
async def test_bug_graph_procedure_failure_falls_back_without_error():
    class BrokenService:
        async def recall_procedures(self, **kwargs):
            raise RuntimeError("unavailable")

    service = object.__new__(BugDiagnosisGraphService)
    service.procedural_memory_service = BrokenService()
    service.procedural_guidance_enabled = True
    service.procedural_observe_only = True
    service.procedural_recall_limit = 3
    service._run_users = {"run-1": "user-1"}

    assert await service._select_procedure({"run_id": "run-1", "environment": "develop"}) == {
        "current_stage": "select_procedure"
    }
