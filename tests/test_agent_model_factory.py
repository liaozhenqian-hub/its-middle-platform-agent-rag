import logging

import pytest
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel

from knowledge.agent_runtime.model_factory import AgentModelFactory
from knowledge.config.settings import Settings


def test_model_factory_builds_openai_responses_model_without_network_calls():
    settings = Settings(
        _env_file=None,
        AGENT_MODEL_PROVIDER="openai",
        AGENT_MODEL_NAME="gpt-5.4-mini",
        AGENT_OPENAI_API_KEY="test-key",
    )

    factory = AgentModelFactory(settings)
    model = factory.create_model()
    run_config = factory.create_run_config("conversation-1")

    assert isinstance(model, OpenAIResponsesModel)
    assert model.model == "gpt-5.4-mini"
    assert run_config.group_id == "conversation-1"
    assert run_config.trace_include_sensitive_data is False
    assert run_config.tracing_disabled is False


def test_model_factory_builds_deepseek_chat_completions_model():
    settings = Settings(
        _env_file=None,
        AGENT_MODEL_PROVIDER="deepseek",
        DEEPSEEK_API_KEY="deepseek-key",
        DEEPSEEK_CHAT_MODEL="deepseek-chat",
        AGENT_TRACING_ENABLED=False,
    )

    factory = AgentModelFactory(settings)
    model = factory.create_model()

    assert isinstance(model, OpenAIChatCompletionsModel)
    assert model.model == "deepseek-chat"
    run_config = factory.create_run_config("conversation-2")
    assert run_config.tracing_disabled is True
    assert run_config.model_settings.extra_body == {
        "thinking": {"type": "disabled"}
    }


def test_model_factory_builds_deepseek_reasoning_model_and_run_config():
    settings = Settings(
        _env_file=None,
        AGENT_MODEL_PROVIDER="deepseek",
        DEEPSEEK_API_KEY="deepseek-key",
        DEEPSEEK_CHAT_MODEL="deepseek-v4-flash",
        DEEPSEEK_REASONING_MODEL="deepseek-v4-pro",
        DEEPSEEK_REASONING_ENABLED=True,
        AGENT_TRACING_ENABLED=False,
    )

    factory = AgentModelFactory(settings)
    model = factory.create_reasoning_model()
    run_config = factory.create_run_config("conversation-reasoning", thinking=True)

    assert isinstance(model, OpenAIChatCompletionsModel)
    assert model.model == "deepseek-v4-pro"
    assert run_config.model_settings.extra_body == {
        "thinking": {"type": "enabled"}
    }


def test_model_factory_reasoning_model_falls_back_to_fast_model_when_disabled():
    settings = Settings(
        _env_file=None,
        AGENT_MODEL_PROVIDER="deepseek",
        DEEPSEEK_API_KEY="deepseek-key",
        DEEPSEEK_CHAT_MODEL="deepseek-v4-flash",
        DEEPSEEK_REASONING_MODEL="deepseek-v4-pro",
        DEEPSEEK_REASONING_ENABLED=False,
        AGENT_TRACING_ENABLED=False,
    )

    factory = AgentModelFactory(settings)
    model = factory.create_reasoning_model()
    run_config = factory.create_run_config("conversation-fallback", thinking=True)

    assert model.model == "deepseek-v4-flash"
    assert run_config.model_settings.extra_body == {
        "thinking": {"type": "disabled"}
    }


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
def test_model_factory_rejects_missing_provider_key(provider):
    settings = Settings(
        _env_file=None,
        AGENT_MODEL_PROVIDER=provider,
        AGENT_OPENAI_API_KEY="",
        DEEPSEEK_API_KEY="",
    )

    with pytest.raises(ValueError, match="API key"):
        AgentModelFactory(settings).create_model()


def test_deepseek_disables_remote_tracing_without_separate_key(caplog):
    settings = Settings(
        _env_file=None,
        AGENT_MODEL_PROVIDER="deepseek",
        DEEPSEEK_API_KEY="deepseek-key",
        AGENT_TRACING_ENABLED=True,
        AGENT_TRACING_API_KEY="",
    )

    with caplog.at_level(logging.WARNING):
        config = AgentModelFactory(settings).create_run_config("conversation-3")

    assert config.tracing_disabled is True
    assert "tracing" in caplog.text.lower()
    assert "deepseek-key" not in caplog.text
