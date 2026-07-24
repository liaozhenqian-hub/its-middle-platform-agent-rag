import logging

from openai import OpenAI

from knowledge.config.app_profiles import AppProfileRegistry
from knowledge.config.settings import Settings
from knowledge.services.query_rewrite_service import LlmQueryRewriteService
from knowledge.services.qwen_rerank_service import QwenRerankService


logger = logging.getLogger(__name__)


def create_query_rewriter(
    settings: Settings,
) -> LlmQueryRewriteService | None:
    if not settings.query_rewrite_enabled:
        logger.info("Query rewrite disabled by configuration")
        return None
    if not settings.resolved_deepseek_api_key:
        logger.warning("Query rewrite unavailable: DeepSeek API key is not configured")
        return None
    client = OpenAI(
        api_key=settings.resolved_deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=settings.query_rewrite_timeout_seconds,
    )
    service = LlmQueryRewriteService(
        client=client,
        model=settings.deepseek_chat_model,
        profiles=AppProfileRegistry.default(),
    )
    logger.info(
        "Query rewrite configured model=%s base_url=%s",
        settings.deepseek_chat_model,
        settings.deepseek_base_url,
    )
    return service


def create_reranker(settings: Settings) -> QwenRerankService | None:
    if not settings.rerank_enabled:
        logger.info("Reranker disabled by configuration")
        return None
    if not settings.resolved_rerank_api_key:
        logger.warning("Reranker unavailable: compatible API key is not configured")
        return None
    client = OpenAI(
        api_key=settings.resolved_rerank_api_key,
        base_url=settings.rerank_base_url,
        timeout=settings.rerank_timeout_seconds,
    )
    service = QwenRerankService(client=client, model=settings.rerank_model)
    logger.info(
        "Reranker configured model=%s base_url=%s",
        settings.rerank_model,
        settings.rerank_base_url,
    )
    return service
