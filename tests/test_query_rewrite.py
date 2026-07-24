import json
from types import SimpleNamespace

from knowledge.config.app_profiles import AppProfileRegistry
from knowledge.services.query_rewrite_service import LlmQueryRewriteService


class FakeCompletions:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self.content, ensure_ascii=False))
                )
            ]
        )


class FakeClient:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)


def test_middle_platform_profile_contains_internal_domains_and_glossary():
    profile = AppProfileRegistry.default().get("middle-platform")

    assert profile.display_name == "中台"
    assert set(profile.domains) == {"指标平台", "审批流", "工作流"}
    assert "指标应用" in profile.glossary


def test_llm_rewriter_returns_structured_query_and_keywords():
    completions = FakeCompletions(
        {
            "retrieval_needed": True,
            "rewritten_query": "指标应用如何通过 getDataV2 开启小计？",
            "keywords": ["指标应用", "getDataV2", "summaryRowFlag", "getDataV2"],
            "domain_candidates": ["指标平台", "不存在的模块"],
            "clarification_needed": False,
        }
    )
    service = LlmQueryRewriteService(
        client=FakeClient(completions),
        model="deepseek-v4-flash",
        profiles=AppProfileRegistry.default(),
    )

    result = service.rewrite("小计怎么开", app_id="middle-platform")

    assert result.retrieval_query == "指标应用如何通过 getDataV2 开启小计？"
    assert result.keywords == ("指标应用", "getDataV2", "summaryRowFlag")
    assert result.domain_candidates == ("指标平台",)
    assert result.retrieval_needed is True
    assert result.rewrite_applied is True
    call = completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "中台" in call["messages"][0]["content"]
    assert "审批流" in call["messages"][0]["content"]


def test_llm_rewriter_can_mark_greeting_as_not_requiring_retrieval():
    service = LlmQueryRewriteService(
        client=FakeClient(
            FakeCompletions(
                {
                    "retrieval_needed": False,
                    "rewritten_query": "你好",
                    "keywords": [],
                    "domain_candidates": [],
                    "clarification_needed": False,
                }
            )
        ),
        model="deepseek-v4-flash",
        profiles=AppProfileRegistry.default(),
    )

    result = service.rewrite("你好", app_id="middle-platform")

    assert result.retrieval_needed is False
    assert result.retrieval_query == "你好"


def test_llm_rewriter_falls_back_to_original_query_on_provider_error(caplog):
    service = LlmQueryRewriteService(
        client=FakeClient(FakeCompletions(error=RuntimeError("provider unavailable"))),
        model="deepseek-v4-flash",
        profiles=AppProfileRegistry.default(),
    )

    with caplog.at_level(
        "WARNING",
        logger="knowledge.services.query_rewrite_service",
    ):
        result = service.rewrite("getDataV2 怎么用", app_id="middle-platform")

    assert result.retrieval_query == "getDataV2 怎么用"
    assert result.keywords == ()
    assert result.retrieval_needed is True
    assert result.rewrite_applied is False
    assert "Query rewrite failed" in caplog.text
    assert "middle-platform" in caplog.text
    assert "getDataV2 怎么用" in caplog.text
    assert "provider unavailable" in caplog.text
