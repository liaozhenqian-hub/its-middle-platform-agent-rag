from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from knowledge.api.app import create_app
from knowledge.catalog.auth import AdminSessionService
from knowledge.catalog.repository import CatalogRepository
from knowledge.config.settings import Settings
from knowledge.quality import QualityCaptureService, QualityRepository, TurnCompletion, TurnStart
from knowledge.quality import QualityAnnotationCreate


class FakeAuthenticator:
    def authenticate(self, username: str, password: str) -> bool:
        return username == "admin" and password == "correct-password"


class FakeEvaluator:
    def __init__(self):
        self.case_ids = None

    async def queue_cases(self, case_ids=None):
        self.case_ids = case_ids
        return SimpleNamespace(
            id="eval-run-1",
            status="queued",
            total_cases=len(case_ids or []),
            passed_cases=len(case_ids or []),
            failed_cases=0,
        )

    async def cancel(self, run_id):
        return SimpleNamespace(id=run_id, status="running", cancel_requested=True)

    async def retry_failed(self, run_id):
        return SimpleNamespace(
            id="eval-run-retry", status="queued", total_cases=1,
            passed_cases=0, failed_cases=0,
        )


@pytest.mark.asyncio
async def test_quality_analytics_and_annotation_review_api(tmp_path):
    repository = QualityRepository(tmp_path / "quality.db")
    await repository.initialize()
    turn = await repository.start_turn(
        TurnStart(
            run_id="run-analytics",
            channel="codex",
            question="工作流连接器怎么配置？",
            domain_id="workflow",
            provider="deepseek",
            model_name="deepseek-v4-flash",
        )
    )
    await repository.complete_turn(
        turn.run_id,
        TurnCompletion(status="completed", answer="有证据的回答", duration_ms=900),
    )
    annotation = await repository.create_annotation(
        QualityAnnotationCreate(
            turn_id=turn.id,
            source="rule",
            code="zero_citation",
            severity="error",
            confidence=1.0,
        )
    )
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    sessions = AdminSessionService(catalog, ttl=timedelta(hours=8))
    app = create_app(
        agent_service=object(),
        component_status={},
        catalog_repository=catalog,
        admin_authenticator=FakeAuthenticator(),
        admin_session_service=sessions,
        runtime_settings=Settings(_env_file=None, ADMIN_COOKIE_SECURE=False),
        quality_capture_service=QualityCaptureService(repository),
    )

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        csrf = login.json()["csrf_token"]
        analytics = client.get(
            "/api/v1/admin/quality/analytics", params={"channel": "codex"}
        )
        assert analytics.status_code == 200
        assert analytics.json()["total_turns"] == 1
        listed = client.get(
            "/api/v1/admin/quality/annotations", params={"code": "zero_citation"}
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        reviewed = client.patch(
            f"/api/v1/admin/quality/annotations/{annotation.id}",
            headers={"X-CSRF-Token": csrf},
            json={"review_status": "confirmed"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["review_status"] == "confirmed"


@pytest.mark.asyncio
async def test_quality_feedback_admin_query_export_eval_and_delete(tmp_path):
    quality_repository = QualityRepository(tmp_path / "quality.db")
    await quality_repository.initialize()
    turn = await quality_repository.start_turn(
        TurnStart(
            run_id="run-1",
            conversation_id="conversation-1",
            channel="web",
            question="审批流为什么超时",
            domain_id="approval-flow",
        )
    )
    await quality_repository.complete_turn(
        "run-1",
        TurnCompletion(
            status="completed",
            answer="需要结合日志继续定位",
            last_agent="Manager Agent",
            duration_ms=88,
        ),
    )
    catalog = CatalogRepository(tmp_path / "catalog.db")
    await catalog.initialize()
    sessions = AdminSessionService(catalog, ttl=timedelta(hours=8))
    evaluator = FakeEvaluator()
    app = create_app(
        agent_service=object(),
        component_status={},
        catalog_repository=catalog,
        admin_authenticator=FakeAuthenticator(),
        admin_session_service=sessions,
        runtime_settings=Settings(_env_file=None, ADMIN_COOKIE_SECURE=False),
        quality_capture_service=QualityCaptureService(quality_repository),
        quality_evaluation_service=evaluator,
    )

    with TestClient(app) as client:
        rejected = client.post(
            f"/api/v1/quality/turns/{turn.id}/feedback",
            json={"feedback_token": "wrong", "rating": "negative", "reason": "引用错误"},
        )
        assert rejected.status_code == 403
        accepted = client.post(
            f"/api/v1/quality/turns/{turn.id}/feedback",
            json={
                "feedback_token": turn.feedback_token,
                "rating": "negative",
                "reason": "引用错误",
            },
        )
        assert accepted.status_code == 204

        assert client.get("/api/v1/admin/quality/turns").status_code == 401
        login = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        csrf = login.json()["csrf_token"]
        page = client.get(
            "/api/v1/admin/quality/turns",
            params={"channel": "web", "rating": "negative", "query": "审批流"},
        )
        assert page.status_code == 200
        assert page.json()["total"] == 1
        assert page.json()["items"][0]["question"] == "审批流为什么超时"
        detail = client.get(f"/api/v1/admin/quality/turns/{turn.id}")
        assert detail.status_code == 200
        assert detail.json()["feedback"][0]["reason"] == "引用错误"

        no_csrf = client.post(
            f"/api/v1/admin/quality/turns/{turn.id}/eval-case",
            json={"name": "审批流超时", "required_facts": ["日志"]},
        )
        assert no_csrf.status_code == 403
        created = client.post(
            f"/api/v1/admin/quality/turns/{turn.id}/eval-case",
            headers={"X-CSRF-Token": csrf},
            json={
                "name": "审批流超时",
                "required_tools": ["bug_diagnosis_expert"],
                "required_citation_types": ["log_trace"],
                "required_facts": ["日志"],
                "forbidden_facts": ["银行卡密码"],
                "tags": ["bug"],
                "expected_behavior": "clarify",
                "max_latency_ms": 45000,
                "max_tool_calls": 4,
                "max_citations": 6,
            },
        )
        assert created.status_code == 201
        assert created.json()["expected_behavior"] == "clarify"
        assert created.json()["max_latency_ms"] == 45000
        assert created.json()["max_tool_calls"] == 4
        assert created.json()["max_citations"] == 6
        case_id = created.json()["id"]
        listed_case = client.get("/api/v1/admin/quality/eval-cases").json()[0]
        assert listed_case["id"] == case_id
        updated = client.put(
            f"/api/v1/admin/quality/eval-cases/{case_id}",
            headers={"X-CSRF-Token": csrf},
            json={
                **{
                    key: listed_case[key]
                    for key in (
                        "name",
                        "question",
                        "knowledge_space_id",
                        "domain_id",
                        "required_tools",
                        "required_citation_types",
                        "required_facts",
                        "forbidden_facts",
                        "tags",
                        "enabled",
                    )
                },
                "expected_behavior": "answer",
                "max_latency_ms": 30000,
                "max_tool_calls": 3,
                "max_citations": 5,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["expected_behavior"] == "answer"
        assert updated.json()["max_tool_calls"] == 3
        eval_run = client.post(
            "/api/v1/admin/quality/eval-runs",
            headers={"X-CSRF-Token": csrf},
            json={"case_ids": [case_id]},
        )
        assert eval_run.status_code == 202
        assert eval_run.json()["id"] == "eval-run-1"
        assert evaluator.case_ids == [case_id]
        cancelled = client.post(
            "/api/v1/admin/quality/eval-runs/eval-run-1/cancel",
            headers={"X-CSRF-Token": csrf},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["cancel_requested"] is True
        retried = client.post(
            "/api/v1/admin/quality/eval-runs/eval-run-1/retry-failed",
            headers={"X-CSRF-Token": csrf},
        )
        assert retried.status_code == 202
        assert retried.json()["status"] == "queued"

        jsonl = client.get("/api/v1/admin/quality/export?format=jsonl")
        assert jsonl.status_code == 200
        assert '"question": "审批流为什么超时"' in jsonl.text
        csv = client.get("/api/v1/admin/quality/export?format=csv")
        assert csv.status_code == 200
        assert "run_id,channel,status" in csv.text

        deleted = client.delete(
            f"/api/v1/admin/quality/turns/{turn.id}",
            headers={"X-CSRF-Token": csrf},
        )
        assert deleted.status_code == 204
        assert client.get(f"/api/v1/admin/quality/turns/{turn.id}").status_code == 404
        assert client.get("/api/v1/admin/quality/eval-cases").json() == []
