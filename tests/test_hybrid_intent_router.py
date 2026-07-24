import pytest

from knowledge.agent_runtime.hybrid_intent_router import HybridDomainIntentRouter
from knowledge.agent_runtime.intent_router import DomainIntentRouter
from knowledge.schemas.documents import QueryRewriteResult


class FakeFallback:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def rewrite(self, message, app_id):
        self.calls.append((message, app_id))
        if self.error:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_hybrid_router_never_calls_flash_for_explicit_rule_match():
    fallback = FakeFallback()
    router = HybridDomainIntentRouter(DomainIntentRouter(), fallback)

    decision = await router.route("审批流管理员转办接口的入参是什么")

    assert decision.domains == ("approval-flow",)
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_hybrid_router_uses_trusted_flash_domain_for_uncertain_question():
    fallback = FakeFallback(QueryRewriteResult(
        original_query="这个编排任务怎么接",
        retrieval_query="工作流编排任务如何对接",
        domain_candidates=("工作流",),
        task_type="how_to",
        rewrite_applied=True,
    ))
    router = HybridDomainIntentRouter(DomainIntentRouter(), fallback)

    decision = await router.route("这个编排任务怎么接")

    assert fallback.calls == [("这个编排任务怎么接", "middle-platform")]
    assert decision.domains == ("workflow",)
    assert decision.intent == "workflow"
    assert decision.task_type == "how_to"
    assert decision.route_source == "flash_fallback"
    assert decision.duration_ms is not None


@pytest.mark.asyncio
async def test_hybrid_router_rejects_unknown_domains_and_degrades_on_failure():
    unknown = HybridDomainIntentRouter(
        DomainIntentRouter(),
        FakeFallback(QueryRewriteResult(
            original_query="内部系统怎么接",
            retrieval_query="内部系统怎么接",
            domain_candidates=("财务系统",),
        )),
    )
    failed = HybridDomainIntentRouter(
        DomainIntentRouter(), FakeFallback(error=TimeoutError("private timeout"))
    )

    assert (await unknown.route("内部系统怎么接")).domains == ()
    assert (await failed.route("内部系统怎么接")).domains == ()
