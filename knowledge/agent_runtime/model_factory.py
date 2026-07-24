import logging

from agents import ModelSettings, RunConfig, set_tracing_export_api_key
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from agents.tracing import gen_trace_id
from openai import AsyncOpenAI

from knowledge.config.settings import Settings


logger = logging.getLogger(__name__)


class AgentModelFactory:
    """Build the server-selected Agents SDK model and per-run trace config."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def create_model(self) -> OpenAIResponsesModel | OpenAIChatCompletionsModel:
        if self.settings.agent_model_provider == "openai":
            api_key = self.settings.resolved_agent_openai_api_key
            if not api_key:
                raise ValueError("OpenAI Agent API key is not configured")
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=self.settings.agent_openai_base_url,
            )
            return OpenAIResponsesModel(
                model=self.settings.agent_model_name,
                openai_client=client,
            )

        return self._create_deepseek_model(self.settings.deepseek_chat_model)

    def create_reasoning_model(
        self,
    ) -> OpenAIResponsesModel | OpenAIChatCompletionsModel:
        """Build the optional deep-reasoning model used only for final Bug analysis."""
        if (
            self.settings.agent_model_provider != "deepseek"
            or not self.settings.deepseek_reasoning_enabled
        ):
            return self.create_model()
        return self._create_deepseek_model(self.settings.deepseek_reasoning_model)

    def _create_deepseek_model(
        self,
        model_name: str,
    ) -> OpenAIChatCompletionsModel:
        api_key = self.settings.resolved_deepseek_api_key
        if not api_key:
            raise ValueError("DeepSeek API key is not configured")
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=self.settings.deepseek_base_url,
        )
        return OpenAIChatCompletionsModel(
            model=model_name,
            openai_client=client,
        )

    def create_run_config(
        self,
        conversation_id: str,
        *,
        thinking: bool = False,
    ) -> RunConfig:
        tracing_enabled = self._configure_tracing()
        model_settings = None
        if self.settings.agent_model_provider == "deepseek":
            model_settings = ModelSettings(
                extra_body={
                    "thinking": {
                        "type": (
                            "enabled"
                            if thinking and self.settings.deepseek_reasoning_enabled
                            else "disabled"
                        )
                    }
                },
            )
        return RunConfig(
            workflow_name="middle-platform-agent",
            group_id=conversation_id,
            trace_id=gen_trace_id(),
            model_settings=model_settings,
            trace_include_sensitive_data=self.settings.agent_trace_include_sensitive_data,
            tracing_disabled=not tracing_enabled,
        )

    def _configure_tracing(self) -> bool:
        if not self.settings.agent_tracing_enabled:
            return False

        tracing_key = self.settings.resolved_agent_tracing_api_key
        if self.settings.agent_model_provider == "deepseek" and not tracing_key:
            logger.warning(
                "Remote Agents tracing disabled for DeepSeek: "
                "AGENT_TRACING_API_KEY is not configured"
            )
            return False

        export_key = tracing_key or self.settings.resolved_agent_openai_api_key
        if not export_key:
            logger.warning("Remote Agents tracing disabled: tracing API key is unavailable")
            return False
        set_tracing_export_api_key(export_key)
        return True
