from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import io
import json
import logging
import re
import secrets
import shutil
import zipfile
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
from pathlib import Path

import httpx
from openai import AsyncOpenAI, OpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from knowledge.agent_runtime.agent_factory import AgentFactory
from knowledge.agent_runtime.conversation_scopes import (
    ConversationScopeConflictError,
    ConversationScopeRepository,
)
from knowledge.agent_runtime.metric_mcp import MetricMCPClient
from knowledge.agent_runtime.model_factory import AgentModelFactory
from knowledge.agent_runtime.pending_runs import (
    PendingRunConflictError,
    PendingRunNotFoundError,
    PendingRunRepository,
)
from knowledge.agent_runtime.pipeline_registry import RetrievalPipelineRegistry
from knowledge.agent_runtime.intent_router import DomainIntentRouter
from knowledge.agent_runtime.hybrid_intent_router import HybridDomainIntentRouter
from knowledge.agent_runtime.service import AgentService
from knowledge.agent_runtime.sessions import AgentSessionFactory
from knowledge.auth.api import create_account_router, create_auth_router
from knowledge.auth.identity import (
    AuthenticationError,
    AuthorizationError,
    require_scope,
)
from knowledge.auth.merge import IdentityMergeService
from knowledge.auth.ownership import ConversationNotFoundError
from knowledge.auth.repository import UserAuthRepository
from knowledge.auth.service import UserAuthService, UserCsrfError
from knowledge.bug_graph.evidence import ContractEvidenceProvider
from knowledge.bug_graph.intake import BugIntakeParser
from knowledge.bug_graph.model import AgentsBugModelAdapter
from knowledge.bug_graph.retrieval import PipelineBugCodeRetriever
from knowledge.bug_graph.service import BugDiagnosisGraphService
from knowledge.api.schemas import (
    AdminLoginRequest,
    AgentResponse,
    CitationDetailResponse,
    ChatRequest,
    ConversationHistoryDetailResponse,
    ConversationHistoryItemResponse,
    ConversationHistoryPageResponse,
    ConversationRenameRequest,
    DecisionsRequest,
    DomainRulesReplaceRequest,
    EvalCaseCreateRequest,
    EvalCaseUpdateRequest,
    EvalRunCreateRequest,
    GitSourceCreateRequest,
    SourceDeleteRequest,
    SwaggerSourceCreateRequest,
    QualityFeedbackRequest,
    QualityAnnotationReviewRequest,
    DomainMemoryPromotionRequest,
)
from knowledge.catalog.auth import (
    AdminSessionService,
    CsrfValidationError,
    InvalidAdminSessionError,
    SharedAdminAuthenticator,
)
from knowledge.catalog.repository import (
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogRepository,
)
from knowledge.catalog.models import (
    KnowledgeSourceCreate,
    SourceDomainRuleCreate,
    SourceType,
    SyncJobState,
)
from knowledge.catalog.secrets import CatalogSecretStore, SecretCipher
from knowledge.indexing.coordinator import SourceIndexCoordinator
from knowledge.logs.grafana import GrafanaLogClient, GrafanaTarget
from knowledge.parsers.uploads import UnsafeArchiveError, extract_upload_archive
from knowledge.quality import (
    CitationSnapshot,
    EvalCaseCreate,
    InvalidFeedbackTokenError,
    QualityCaptureService,
    QualityEvaluationService,
    QualityNotFoundError,
    QualityRepository,
    QualitySpanSnapshot,
    ToolRunSnapshot,
    TurnCompletion,
    TurnStart,
)
from knowledge.quality.judge import DeepSeekSemanticJudge
from knowledge.quality.worker import QualityEvalWorker
from knowledge.config.logging_config import configure_logging
from knowledge.config.settings import Settings
from knowledge.feishu.bridge import FeishuBotBridge
from knowledge.feishu.gateway import LarkOapiGateway
from knowledge.feishu.repository import FeishuEventRepository
from knowledge.memory.extractor import MemoryExtractor
from knowledge.memory.index import MemoryIndex
from knowledge.memory.incidents import BugIncidentMemoryRecorder
from knowledge.memory.entities import EntityMemoryRepository
from knowledge.memory.repository import MemoryRepository
from knowledge.memory.service import MemoryService
from knowledge.memory.summarizer import ConversationSummaryService
from knowledge.memory.worker import MemoryExtractionWorker
from knowledge.history.service import (
    ConversationHistoryNotFound,
    ConversationHistoryService,
)
from knowledge.repositories.vector_store_repository import VectorStoreRepository
from knowledge.source_sync import (
    GitLabClient,
    GitRepositoryManager,
    GitSourceJobProcessor,
    SourceSyncWorker,
)
from knowledge.source_sync.processors import (
    DeleteSourceJobProcessor,
    DocumentSourceJobProcessor,
    SourceJobRouter,
)
from knowledge.services.citation_detail_service import (
    CitationDetailNotFound,
    CitationDetailService,
)
from knowledge.swagger.catalog import CatalogSwaggerCache, CatalogSwaggerSourceProvider
from knowledge.swagger.inspector import SwaggerInspector


logger = logging.getLogger(__name__)

def _sse(event: dict[str, Any]) -> str:
    return (
        f"event: {event['event']}\n"
        f"data: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
    )


def create_app(
    agent_service: Any | None = None,
    component_status: dict[str, dict[str, Any]] | None = None,
    catalog_repository: CatalogRepository | None = None,
    admin_authenticator: Any | None = None,
    admin_session_service: AdminSessionService | None = None,
    runtime_settings: Settings | None = None,
    gitlab_client: Any | None = None,
    catalog_secret_store: CatalogSecretStore | None = None,
    citation_detail_service: Any | None = None,
    quality_capture_service: Any | None = None,
    quality_evaluation_service: Any | None = None,
    memory_service: Any | None = None,
    memory_repository: Any | None = None,
    user_auth_service: Any | None = None,
    conversation_history_service: Any | None = None,
) -> FastAPI:
    injected_service = agent_service
    injected_status = component_status
    injected_catalog = catalog_repository
    injected_admin_authenticator = admin_authenticator
    injected_admin_sessions = admin_session_service
    injected_settings = runtime_settings
    injected_gitlab_client = gitlab_client
    injected_secret_store = catalog_secret_store
    injected_citation_detail = citation_detail_service
    injected_quality_capture = quality_capture_service
    injected_quality_evaluator = quality_evaluation_service
    injected_memory_service = memory_service
    injected_memory_repository = memory_repository
    injected_user_auth_service = user_auth_service
    injected_conversation_history = conversation_history_service

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if injected_service is not None:
            application.state.agent_service = injected_service
            application.state.component_status = injected_status or {}
            application.state.catalog = injected_catalog
            application.state.admin_authenticator = injected_admin_authenticator
            application.state.admin_sessions = injected_admin_sessions
            application.state.settings = injected_settings or Settings()
            application.state.gitlab_client = injected_gitlab_client
            application.state.catalog_secret_store = injected_secret_store
            application.state.citation_detail_service = injected_citation_detail
            application.state.quality_capture_service = injected_quality_capture
            application.state.quality_evaluation_service = injected_quality_evaluator
            application.state.memory_service = injected_memory_service
            application.state.memory_repository = injected_memory_repository
            application.state.user_auth_service = injected_user_auth_service
            application.state.conversation_history_service = injected_conversation_history
            yield
            return

        settings = Settings()
        configure_logging(settings)
        user_auth_service = None
        user_auth_repository = None
        user_auth_status = "disabled"
        if settings.user_auth_enabled:
            try:
                user_auth_repository = UserAuthRepository(
                    settings.resolved_user_auth_db
                )
                await user_auth_repository.initialize()
                await user_auth_repository.cleanup_expired()
                user_auth_service = UserAuthService(
                    settings, user_auth_repository
                )
                user_auth_status = "available"
            except Exception as exc:
                logger.warning(
                    "User authentication unavailable during startup error_type=%s",
                    type(exc).__name__,
                )
                user_auth_service = None
                user_auth_status = "unavailable"
        pending_runs = PendingRunRepository(settings.resolved_agent_session_db)
        await pending_runs.initialize()
        conversation_scopes = ConversationScopeRepository(
            settings.resolved_agent_session_db
        )
        await conversation_scopes.initialize()
        catalog = CatalogRepository(settings.resolved_knowledge_catalog_db)
        await catalog.initialize()
        memory_repository = None
        memory_service = None
        memory_worker = None
        entity_memory_repository = None
        memory_status = "disabled"
        memory_worker_status = "disabled"
        if settings.memory_enabled:
            try:
                memory_repository = MemoryRepository(settings.resolved_memory_db)
                await memory_repository.initialize()
                await memory_repository.expire_memories()
                entity_memory_repository = EntityMemoryRepository(
                    settings.resolved_memory_db
                )
                await entity_memory_repository.initialize()
                memory_index = None
                if settings.resolved_embedding_api_key:
                    memory_index = MemoryIndex(VectorStoreRepository.from_settings(
                        settings,
                        collection_name=settings.memory_chroma_collection_name,
                    ))
                extractor = None
                if settings.memory_extraction_enabled and settings.resolved_deepseek_api_key:
                    extractor = MemoryExtractor(
                        client=OpenAI(
                            api_key=settings.resolved_deepseek_api_key,
                            base_url=settings.deepseek_base_url,
                            timeout=settings.query_rewrite_timeout_seconds,
                        ),
                        model=settings.deepseek_chat_model,
                    )
                memory_service = MemoryService(
                    memory_repository,
                    extractor=extractor,
                    index=memory_index,
                    max_recall=settings.memory_max_recall,
                    candidate_ttl_seconds=settings.memory_candidate_ttl_seconds,
                    default_retention_days=settings.memory_default_retention_days,
                    auto_confirm_seconds=settings.memory_auto_confirm_seconds,
                )
                memory_status = "available"
                if user_auth_service is not None and user_auth_repository is not None:
                    user_auth_service.merge_service = IdentityMergeService(
                        user_auth_repository, memory_repository
                    )
                if settings.memory_worker_enabled:
                    memory_worker = MemoryExtractionWorker(
                        repository=memory_repository,
                        memory_service=memory_service,
                        summary_service=ConversationSummaryService(
                            memory_repository,
                            max_chars=settings.memory_summary_max_chars,
                        ),
                        poll_seconds=settings.memory_worker_poll_seconds,
                        stale_seconds=settings.memory_worker_stale_seconds,
                        maintenance_seconds=settings.memory_maintenance_interval_seconds,
                    )
                    await memory_worker.start()
                    application.state._runtime_cleanup.push_async_callback(memory_worker.close)
                    memory_worker_status = "available"
            except Exception:
                logger.warning("Long-term memory unavailable during startup", exc_info=True)
                memory_repository = None
                memory_service = None
                memory_worker = None
                entity_memory_repository = None
                memory_status = "unavailable"
        quality_capture = None
        quality_repository = None
        quality_status = "disabled"
        if settings.agent_quality_enabled:
            try:
                quality_repository = QualityRepository(settings.resolved_agent_quality_db)
                await quality_repository.initialize()
                await quality_repository.recover_stale_running(
                    settings.agent_quality_running_timeout_seconds
                )
                quality_capture = QualityCaptureService(
                    quality_repository,
                    memory_repository=(
                        memory_repository
                        if memory_service is not None and memory_service.extractor is not None
                        else None
                    ),
                )
                quality_status = (
                    "available" if await quality_repository.check_ready() else "unavailable"
                )
            except Exception:
                logger.warning("Agent quality database unavailable", exc_info=True)
                quality_capture = None
                quality_status = "unavailable"
        secret_store = (
            CatalogSecretStore(
                catalog,
                SecretCipher(settings.resolved_knowledge_secret_master_key),
            )
            if settings.resolved_knowledge_secret_master_key
            else None
        )
        integration_http_client = httpx.AsyncClient()
        application.state._runtime_cleanup.push_async_callback(
            integration_http_client.aclose
        )
        swagger_cache = CatalogSwaggerCache(catalog)
        swagger_inspector = SwaggerInspector(
            integration_http_client,
            swagger_cache,
            settings.swagger_allowed_host_set,
        )
        swagger_source_provider = CatalogSwaggerSourceProvider(
            catalog,
            secret_store,
        )
        production_gitlab_client = (
            GitLabClient(
                integration_http_client,
                settings.gitlab_base_url,
                settings.resolved_gitlab_access_token,
            )
            if settings.gitlab_base_url.strip()
            and settings.resolved_gitlab_access_token
            else None
        )
        grafana_log_client = None
        grafana_status = "disabled"
        if settings.grafana_log_enabled:
            configured_targets = settings.grafana_log_targets
            configuration_complete = bool(
                settings.grafana_log_url.strip()
                and settings.resolved_grafana_log_bearer_token
                and settings.grafana_log_app_label.strip()
                and all(
                    all(part.strip() for part in target)
                    for target in configured_targets.values()
                )
            )
            if configuration_complete:
                grafana_log_client = GrafanaLogClient(
                    integration_http_client,
                    url=settings.grafana_log_url,
                    token=settings.resolved_grafana_log_bearer_token,
                    targets={
                        environment: GrafanaTarget(*target)
                        for environment, target in configured_targets.items()
                    },
                    timeout_seconds=settings.grafana_log_timeout_seconds,
                    max_entries=settings.grafana_log_max_entries,
                    max_entry_chars=settings.grafana_log_max_entry_chars,
                    max_total_chars=settings.grafana_log_max_total_chars,
                    max_time_range_minutes=settings.grafana_log_max_range_minutes,
                    app_label=settings.grafana_log_app_label,
                    query_max_lines=settings.grafana_log_query_max_lines,
                )
                grafana_status = "available"
            else:
                grafana_status = "unavailable"

        model_factory = AgentModelFactory(settings)
        model = model_factory.create_model()
        registry = RetrievalPipelineRegistry(settings=settings)
        await asyncio.to_thread(
            registry.warm,
            [
                ("middle-platform", "指标平台"),
                ("middle-platform", "审批流"),
                ("middle-platform", "工作流"),
                ("middle-platform", None),
            ],
        )
        mcp_client = MetricMCPClient(settings)
        application.state._runtime_cleanup.push_async_callback(mcp_client.close)
        await mcp_client.connect()
        bug_graph_service = None
        bug_graph_status = "disabled"
        if settings.bug_graph_enabled:
            if grafana_log_client is None:
                bug_graph_status = "unavailable"
            else:
                try:
                    settings.resolved_bug_graph_db.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )
                    saver_context = AsyncSqliteSaver.from_conn_string(
                        str(settings.resolved_bug_graph_db)
                    )
                    saver = await application.state._runtime_cleanup.enter_async_context(
                        saver_context
                    )
                    await saver.setup()
                    diagnosis_model = model
                    if (
                        settings.agent_model_provider == "deepseek"
                        and settings.deepseek_reasoning_enabled
                    ):
                        diagnosis_model = model_factory.create_reasoning_model()
                    bug_model = AgentsBugModelAdapter(
                        model=model,
                        diagnosis_model=diagnosis_model,
                        conversation_id="bug-graph",
                        run_config_factory=model_factory.create_run_config,
                        diagnosis_run_config_factory=lambda conversation_id: (
                            model_factory.create_run_config(
                                conversation_id,
                                thinking=True,
                            )
                        ),
                    )
                    bug_graph_service = BugDiagnosisGraphService(
                        checkpointer=saver,
                        log_client=grafana_log_client,
                        code_retriever=PipelineBugCodeRetriever(
                            registry,
                            top_k=settings.bug_graph_code_top_k,
                            min_rerank_score=settings.bug_graph_min_rerank_score,
                        ),
                        intake_parser=BugIntakeParser(bug_model),
                        diagnosis_generator=bug_model,
                        evidence_enricher=ContractEvidenceProvider(
                            registry=registry,
                            swagger_inspector=swagger_inspector,
                            swagger_source_provider=swagger_source_provider,
                        ),
                        context_resolver=quality_repository,
                        incident_recorder=(
                            BugIncidentMemoryRecorder(
                                memory_repository,
                                candidate_ttl_seconds=(
                                    settings.memory_incident_candidate_ttl_seconds
                                ),
                                entity_repository=entity_memory_repository,
                                procedural_enabled=settings.memory_procedural_enabled,
                            )
                            if memory_repository is not None
                            else None
                        ),
                        entity_memory_repository=entity_memory_repository,
                        entity_recall_limit=settings.memory_entity_recall_limit,
                        procedural_memory_service=memory_service,
                        procedural_guidance_enabled=settings.memory_procedural_guidance_enabled,
                        procedural_observe_only=settings.memory_procedural_observe_only,
                        procedural_recall_limit=settings.memory_procedural_recall_limit,
                        interrupt_ttl_seconds=settings.bug_graph_interrupt_ttl_seconds,
                        log_retry_count=settings.bug_graph_log_retry_count,
                        log_range_minutes=settings.bug_graph_log_range_minutes,
                    )
                    await bug_graph_service.start()
                    application.state._runtime_cleanup.push_async_callback(
                        bug_graph_service.close
                    )
                    bug_graph_status = "available"
                except Exception:
                    logger.warning(
                        "Bug diagnosis graph unavailable during startup",
                        exc_info=True,
                    )
                    bug_graph_service = None
                    bug_graph_status = "unavailable"
        topology = AgentFactory(
            model=model,
            registry=registry,
            metric_mcp_server=mcp_client.server if mcp_client.available else None,
            swagger_inspector=swagger_inspector,
            swagger_source_provider=swagger_source_provider,
            bug_graph_service=bug_graph_service,
            metric_query_guard_enabled=settings.metric_query_guard_enabled,
            retrieval_max_calls=settings.agent_retrieval_max_calls,
            retrieval_max_identical_queries=(
                settings.agent_retrieval_max_identical_queries
            ),
            composite_evidence_enabled=settings.agent_composite_evidence_enabled,
            memory_service=memory_service,
            entity_memory_repository=entity_memory_repository,
        ).create()
        service = AgentService(
            manager=topology.manager,
            domain_managers=(
                getattr(topology, "domain_managers", {})
                if settings.agent_direct_specialist_enabled
                else {}
            ),
            intent_router=(
                HybridDomainIntentRouter(
                    DomainIntentRouter(),
                    getattr(registry, "query_rewriter", None)
                    if settings.agent_llm_router_enabled
                    else None,
                )
                if settings.agent_intent_router_enabled
                else None
            ),
            intent_router_min_confidence=settings.agent_intent_router_min_confidence,
            model_factory=model_factory,
            session_factory=AgentSessionFactory(
                settings.resolved_agent_session_db,
                settings.agent_session_history_limit,
            ),
            pending_runs=pending_runs,
            scope_repository=conversation_scopes,
            max_turns=settings.agent_max_turns,
            public_citation_limit=settings.agent_public_citation_limit,
            bug_graph_service=bug_graph_service,
            memory_service=memory_service,
        )
        semantic_judge = None
        if (
            quality_repository is not None
            and settings.agent_quality_semantic_judge_enabled
            and settings.resolved_deepseek_api_key
        ):
            judge_client = AsyncOpenAI(
                api_key=settings.resolved_deepseek_api_key,
                base_url=settings.deepseek_base_url,
                timeout=settings.agent_quality_eval_case_timeout_seconds,
            )
            application.state._runtime_cleanup.push_async_callback(judge_client.close)
            semantic_judge = DeepSeekSemanticJudge(
                client=judge_client,
                model=settings.deepseek_reasoning_model,
            )
        quality_evaluator = (
            QualityEvaluationService(
                repository=quality_repository,
                agent_service=service,
                application_version=settings.agent_application_version,
                provider=settings.agent_model_provider,
                model_name=(
                    settings.agent_model_name
                    if settings.agent_model_provider == "openai"
                    else settings.deepseek_chat_model
                ),
                semantic_judge=semantic_judge,
                case_timeout_seconds=settings.agent_quality_eval_case_timeout_seconds,
                run_config_snapshot={
                    "prompt_version": settings.agent_prompt_version,
                    "knowledge_count": registry.repository.count(),
                    "direct_specialist": settings.agent_direct_specialist_enabled,
                    "composite_evidence": settings.agent_composite_evidence_enabled,
                },
            )
            if quality_repository is not None
            else None
        )
        quality_eval_worker = None
        if quality_evaluator is not None and settings.agent_quality_eval_worker_enabled:
            quality_eval_worker = QualityEvalWorker(
                repository=quality_repository,
                evaluator=quality_evaluator,
                poll_seconds=settings.agent_quality_eval_poll_seconds,
                stale_seconds=settings.agent_quality_eval_stale_seconds,
                scheduled=settings.agent_quality_scheduled_eval_enabled,
            )
            await quality_eval_worker.start()
            application.state._runtime_cleanup.push_async_callback(
                quality_eval_worker.close
            )
        citation_details = CitationDetailService(
            catalog=catalog,
            vector_repository=registry.repository,
            max_chars=settings.citation_detail_max_chars,
            storage_root=settings.resolved_knowledge_storage_root,
        )
        feishu_bridge = None
        feishu_status = "disabled"
        if settings.feishu_bot_enabled:
            gateway = LarkOapiGateway(
                settings.resolved_feishu_app_id,
                settings.resolved_feishu_app_secret,
            )
            feishu_bridge = FeishuBotBridge(
                gateway=gateway,
                agent_service=service,
                repository=FeishuEventRepository(settings.resolved_feishu_event_db),
                quality_capture=quality_capture,
                reply_max_chars=settings.feishu_reply_max_chars,
                agent_timeout_seconds=settings.feishu_agent_timeout_seconds,
                require_group_mention=settings.feishu_group_require_mention,
                provider=settings.agent_model_provider,
                model_name=(
                    settings.agent_model_name
                    if settings.agent_model_provider == "openai"
                    else settings.deepseek_chat_model
                ),
                thread_isolation_enabled=settings.feishu_thread_isolation_enabled,
                ownership_service=(
                    user_auth_service.ownership
                    if user_auth_service is not None
                    else None
                ),
            )
            try:
                await feishu_bridge.start()
            except Exception as exc:
                logger.warning(
                    "Feishu bot unavailable during startup error_type=%s",
                    type(exc).__name__,
                )
                try:
                    await feishu_bridge.close()
                except Exception as close_error:
                    logger.warning(
                        "Feishu bot cleanup failed error_type=%s",
                        type(close_error).__name__,
                    )
                feishu_bridge = None
                feishu_status = "unavailable"
            else:
                application.state._runtime_cleanup.push_async_callback(
                    feishu_bridge.close
                )
                feishu_status = "available"
        source_worker = None
        if settings.source_worker_enabled:
            index_coordinator = SourceIndexCoordinator(
                catalog,
                registry.repository,
                registry,
            )
            git_processor = GitSourceJobProcessor(
                catalog,
                GitRepositoryManager(
                    settings.resolved_knowledge_storage_root,
                    settings.resolved_gitlab_access_token,
                    command_timeout_seconds=settings.git_command_timeout_seconds,
                ),
                index_coordinator,
            )
            processor = SourceJobRouter(
                catalog,
                git_processor=git_processor,
                document_processor=DocumentSourceJobProcessor(
                    catalog, index_coordinator
                ),
                delete_processor=DeleteSourceJobProcessor(
                    catalog,
                    index_coordinator,
                    settings.resolved_knowledge_storage_root,
                    secret_store=secret_store,
                ),
            )
            source_worker = SourceSyncWorker(
                catalog,
                processor,
                worker_id=f"source-worker-{uuid4()}",
                poll_seconds=settings.source_worker_poll_seconds,
                stale_after_seconds=settings.source_worker_stale_seconds,
                scan_interval_seconds=settings.git_sync_interval_seconds,
                gitlab_client=production_gitlab_client,
            )
            application.state._runtime_cleanup.push_async_callback(source_worker.stop)
            await source_worker.start()
        application.state.agent_service = service
        application.state.catalog = catalog
        application.state.conversation_scopes = conversation_scopes
        application.state.admin_sessions = AdminSessionService(
            catalog,
            ttl=timedelta(seconds=settings.admin_session_ttl_seconds),
        )
        application.state.admin_authenticator = (
            SharedAdminAuthenticator(
                username=settings.admin_username,
                password_hash=settings.admin_password_hash,
            )
            if settings.admin_username and settings.admin_password_hash
            else None
        )
        application.state.settings = settings
        application.state.gitlab_client = production_gitlab_client
        application.state.catalog_secret_store = secret_store
        application.state.swagger_inspector = swagger_inspector
        application.state.swagger_source_provider = swagger_source_provider
        application.state.source_worker = source_worker
        application.state.metric_mcp_client = mcp_client
        application.state.grafana_log_client = grafana_log_client
        application.state.bug_graph_service = bug_graph_service
        application.state.feishu_bot_bridge = feishu_bridge
        application.state.citation_detail_service = citation_details
        application.state.quality_capture_service = quality_capture
        application.state.quality_evaluation_service = quality_evaluator
        application.state.quality_eval_worker = quality_eval_worker
        application.state.memory_service = memory_service
        application.state.memory_repository = memory_repository
        application.state.memory_worker = memory_worker
        application.state.entity_memory_repository = entity_memory_repository
        application.state.user_auth_service = user_auth_service
        application.state.conversation_history_service = (
            ConversationHistoryService(
                user_auth_repository, settings.resolved_agent_session_db
            )
            if user_auth_repository is not None
            else None
        )
        application.state.component_status = {
            "model": {
                "status": "available",
                "provider": settings.agent_model_provider,
                "model": (
                    settings.agent_model_name
                    if settings.agent_model_provider == "openai"
                    else settings.deepseek_chat_model
                ),
                "reasoning_model": (
                    settings.deepseek_reasoning_model
                    if (
                        settings.agent_model_provider == "deepseek"
                        and settings.deepseek_reasoning_enabled
                    )
                    else None
                ),
            },
            "sqlite": {
                "status": "available"
                if await pending_runs.check_ready()
                else "unavailable"
            },
            "chroma": {
                "status": "available",
                "collection": settings.chroma_collection_name,
                "count": registry.repository.count(),
            },
            "mcp": {"status": mcp_client.status},
            "grafana_logs": {"status": grafana_status},
            "bug_graph": {"status": bug_graph_status},
            "feishu_bot": {"status": feishu_status},
            "catalog": {
                "status": "available" if await catalog.check_ready() else "unavailable"
            },
            "agent_quality": {"status": quality_status},
            "quality_eval_worker": {
                "status": "available" if quality_eval_worker is not None else "disabled"
            },
            "long_term_memory": {"status": memory_status},
            "memory_worker": {"status": memory_worker_status},
            "entity_memory": {
                "status": "available"
                if entity_memory_repository is not None
                else "disabled"
            },
            "user_auth": {"status": user_auth_status},
            "worker": {
                "status": (
                    "available"
                    if source_worker is not None
                    else "disabled"
                )
            },
            "gitlab": {
                "status": (
                    "available"
                    if production_gitlab_client is not None
                    else "unavailable"
                )
            },
            "swagger_cache": {"status": "available"},
        }
        yield

    @asynccontextmanager
    async def guarded_lifespan(application: FastAPI):
        cleanup = AsyncExitStack()
        await cleanup.__aenter__()
        application.state._runtime_cleanup = cleanup
        try:
            async with lifespan(application):
                yield
        finally:
            try:
                await cleanup.aclose()
            finally:
                application.state._runtime_cleanup = None

    application = FastAPI(
        title="Middle Platform Agent API",
        version="0.1.0",
        lifespan=guarded_lifespan,
    )
    application.include_router(create_auth_router())
    application.include_router(create_account_router())
    if injected_service is not None:
        application.state.agent_service = injected_service
        application.state.component_status = injected_status or {}
        application.state.catalog = injected_catalog
        application.state.admin_authenticator = injected_admin_authenticator
        application.state.admin_sessions = injected_admin_sessions
        application.state.settings = injected_settings or Settings()
        application.state.gitlab_client = injected_gitlab_client
        application.state.catalog_secret_store = injected_secret_store
        application.state.citation_detail_service = injected_citation_detail
        application.state.quality_capture_service = injected_quality_capture
        application.state.quality_evaluation_service = injected_quality_evaluator
        application.state.memory_service = injected_memory_service
        application.state.memory_repository = injected_memory_repository
        application.state.user_auth_service = injected_user_auth_service
        application.state.conversation_history_service = injected_conversation_history

    @application.exception_handler(PendingRunNotFoundError)
    async def pending_not_found(request: Request, exc: PendingRunNotFoundError):
        return JSONResponse(status_code=404, content={"detail": "pending run not found"})

    @application.exception_handler(PendingRunConflictError)
    async def pending_conflict(request: Request, exc: PendingRunConflictError):
        return JSONResponse(status_code=409, content={"detail": "run already processed"})

    @application.exception_handler(ConversationScopeConflictError)
    async def conversation_scope_conflict(
        request: Request, exc: ConversationScopeConflictError
    ):
        return JSONResponse(
            status_code=409,
            content={"detail": "conversation is bound to a different knowledge scope"},
        )

    @application.exception_handler(InvalidAdminSessionError)
    async def invalid_admin_session(request: Request, exc: InvalidAdminSessionError):
        return JSONResponse(status_code=401, content={"detail": "admin session is invalid"})

    @application.exception_handler(CsrfValidationError)
    async def invalid_csrf(request: Request, exc: CsrfValidationError):
        return JSONResponse(status_code=403, content={"detail": "CSRF token is invalid"})

    @application.exception_handler(ConversationNotFoundError)
    async def conversation_owner_not_found(
        request: Request, exc: ConversationNotFoundError
    ):
        return JSONResponse(status_code=404, content={"detail": "conversation not found"})

    @application.exception_handler(ConversationHistoryNotFound)
    async def conversation_history_not_found(
        request: Request, exc: ConversationHistoryNotFound
    ):
        return JSONResponse(status_code=404, content={"detail": "conversation not found"})

    def _admin_resources(request: Request):
        settings = getattr(request.app.state, "settings", None)
        sessions = getattr(request.app.state, "admin_sessions", None)
        if settings is None or sessions is None:
            raise HTTPException(status_code=503, detail="admin authentication unavailable")
        return settings, sessions

    async def _require_admin_read(request: Request):
        settings, sessions = _admin_resources(request)
        token = request.cookies.get(settings.admin_cookie_name, "")
        if not token:
            raise InvalidAdminSessionError("admin session is missing")
        return await sessions.validate_read_only(token)

    async def _require_admin_write(request: Request):
        settings, sessions = _admin_resources(request)
        token = request.cookies.get(settings.admin_cookie_name, "")
        if not token:
            raise InvalidAdminSessionError("admin session is missing")
        return await sessions.validate(
            token,
            csrf_token=request.headers.get("X-CSRF-Token"),
        )

    def _quality_channel(request: Request) -> str:
        requested = request.headers.get("X-Client-Channel", "").lower()
        return requested if requested in {"web", "codex"} else "api"

    async def _resolve_user_identity(request: Request, scope: str | None = None):
        service = getattr(request.app.state, "user_auth_service", None)
        if service is None:
            return None
        try:
            resolution = await service.resolve(
                authorization=request.headers.get("Authorization"),
                user_session_cookie=request.cookies.get(
                    service.settings.user_session_cookie_name
                ),
                anonymous_cookie=request.cookies.get(
                    service.settings.anonymous_cookie_name
                ),
            )
            if scope:
                require_scope(resolution.identity, scope)
            request.state.user_identity = resolution.identity
            return resolution
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=401, detail="invalid bearer credentials"
            ) from exc
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def _apply_user_identity_cookies(response, request: Request, resolution) -> None:
        if resolution is None:
            return
        service = request.app.state.user_auth_service
        settings = service.settings
        if resolution.cookie_value:
            response.set_cookie(
                settings.anonymous_cookie_name,
                resolution.cookie_value,
                max_age=settings.anonymous_device_ttl_seconds,
                httponly=True,
                secure=settings.user_cookie_secure,
                samesite="strict",
                path="/",
            )
        if resolution.clear_user_cookie:
            response.delete_cookie(
                settings.user_session_cookie_name,
                path="/",
                secure=settings.user_cookie_secure,
                httponly=True,
                samesite="strict",
            )
        elif resolution.identity.kind == "feishu":
            current = request.cookies.get(settings.user_session_cookie_name)
            if current:
                response.set_cookie(
                    settings.user_session_cookie_name,
                    current,
                    max_age=settings.user_session_sliding_ttl_seconds,
                    httponly=True,
                    secure=settings.user_cookie_secure,
                    samesite="strict",
                    path="/",
                )

    async def _start_quality_turn(
        request: Request,
        body: ChatRequest,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str | None = None,
    ):
        capture = getattr(request.app.state, "quality_capture_service", None)
        if capture is None:
            return None
        settings = getattr(request.app.state, "settings", None) or Settings(_env_file=None)
        provider = settings.agent_model_provider
        model_name = (
            settings.agent_model_name
            if provider == "openai"
            else settings.deepseek_chat_model
        )
        try:
            return await capture.start(
                TurnStart(
                    run_id=run_id,
                    conversation_id=conversation_id,
                    channel=_quality_channel(request),
                    user_id=user_id,
                    question=body.message,
                    knowledge_space_id=body.knowledge_space_id or "middle-platform",
                    domain_id=body.domain_id,
                    provider=provider,
                    model_name=model_name,
                    application_version=settings.agent_application_version,
                    prompt_version=settings.agent_prompt_version,
                )
            )
        except Exception as exc:
            logger.warning(
                "Quality turn start failed run_id=%s error_type=%s",
                run_id,
                type(exc).__name__,
            )
            return None

    async def _complete_quality_turn(
        request: Request,
        run_id: str,
        *,
        status: str,
        duration_ms: float,
        response: dict[str, Any] | None = None,
        answer: str | None = None,
        error_type: str | None = None,
    ) -> None:
        capture = getattr(request.app.state, "quality_capture_service", None)
        if capture is None:
            return
        response = response or {}
        tools = [
            ToolRunSnapshot(
                tool_call_id=str(item.get("tool_call_id") or ""),
                tool_name=str(item.get("tool_name") or "unknown"),
                agent_name=str(item.get("agent_name") or ""),
                status=str(item.get("status") or "unknown"),
                duration_ms=item.get("duration_ms"),
                arguments=item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
            )
            for item in response.get("tool_runs", [])
            if isinstance(item, dict)
        ]
        citations = [
            CitationSnapshot(
                source_type=str(item.get("source_type") or ""),
                source_id=str(item.get("source_id") or ""),
                title=str(item.get("title") or ""),
                domain=str(item.get("domain") or ""),
                metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            )
            for item in response.get("citations", [])
            if isinstance(item, dict)
        ]
        spans = [
            QualitySpanSnapshot(
                kind=str(item.get("kind") or "agent"),
                name=str(item.get("name") or "unknown"),
                status=str(item.get("status") or "unknown"),
                duration_ms=item.get("duration_ms"),
                input_tokens=int(item.get("input_tokens") or 0),
                output_tokens=int(item.get("output_tokens") or 0),
                total_tokens=int(item.get("total_tokens") or 0),
                metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            )
            for item in response.get("quality_spans", [])
            if isinstance(item, dict)
        ]
        try:
            routed_domains = [
                str(item)
                for item in response.get("routed_domains", [])
                if str(item).strip()
            ]
            await capture.complete(
                run_id,
                TurnCompletion(
                    status=status,
                    answer=response.get("answer") if response else answer,
                    last_agent=str(response.get("last_agent") or ""),
                    domain_id=(
                        routed_domains[0] if len(routed_domains) == 1 else None
                    ),
                    duration_ms=duration_ms,
                    error_type=error_type,
                    routed_domains=routed_domains,
                    specialists_used=list(
                        dict.fromkeys(
                            item.tool_name
                            for item in tools
                            if item.tool_name
                            in {
                                "approval_flow_expert",
                                "workflow_expert",
                                "metric_platform_expert",
                                "bug_diagnosis_expert",
                            }
                        )
                    ),
                    response_mode=str(response.get("response_mode") or "answer"),
                    spans=spans,
                    tools=tools,
                    citations=citations,
                ),
            )
        except Exception as exc:
            logger.warning(
                "Quality turn completion failed run_id=%s error_type=%s",
                run_id,
                type(exc).__name__,
            )

    @application.post("/api/v1/agent/chat", response_model=AgentResponse)
    async def chat(body: ChatRequest, request: Request, response: Response):
        run_id = str(uuid4())
        conversation_id = body.conversation_id or str(uuid4())
        identity_resolution = await _resolve_user_identity(request, "agent:query")
        if identity_resolution is not None:
            await request.app.state.user_auth_service.ownership.claim(
                conversation_id,
                identity_resolution.identity.owner_id,
                channel=_quality_channel(request),
            )
            _apply_user_identity_cookies(response, request, identity_resolution)
        started_at = perf_counter()
        quality_turn = await _start_quality_turn(
            request,
            body,
            run_id=run_id,
            conversation_id=conversation_id,
            user_id=(
                identity_resolution.identity.owner_id
                if identity_resolution is not None
                else None
            ),
        )
        scope_provided = bool(
            {"knowledge_space_id", "domain_id"} & body.model_fields_set
        )
        try:
            result = await request.app.state.agent_service.chat(
                body.message,
                conversation_id,
                run_id=run_id,
                knowledge_space_id=body.knowledge_space_id,
                domain_id=body.domain_id,
                scope_provided=scope_provided,
                user_id=(
                    identity_resolution.identity.owner_id
                    if identity_resolution is not None
                    else None
                ),
            )
            payload = result.to_dict()
            await _complete_quality_turn(
                request,
                run_id,
                status=result.status,
                duration_ms=(perf_counter() - started_at) * 1000,
                response=payload,
            )
            payload.pop("quality_spans", None)
            payload["quality_turn_id"] = getattr(quality_turn, "id", None)
            payload["feedback_token"] = getattr(quality_turn, "feedback_token", None)
            return AgentResponse.model_validate(payload)
        except Exception as exc:
            await _complete_quality_turn(
                request,
                run_id,
                status="error",
                duration_ms=(perf_counter() - started_at) * 1000,
                error_type=type(exc).__name__,
            )
            raise

    @application.post("/api/v1/agent/chat/stream")
    async def chat_stream(body: ChatRequest, request: Request):
        identity_resolution = await _resolve_user_identity(request, "agent:query")
        scope_provided = bool(
            {"knowledge_space_id", "domain_id"} & body.model_fields_set
        )
        conversation_id = await request.app.state.agent_service.prepare_conversation_scope(
            body.conversation_id,
            knowledge_space_id=body.knowledge_space_id,
            domain_id=body.domain_id,
            scope_provided=scope_provided,
        )
        if identity_resolution is not None:
            await request.app.state.user_auth_service.ownership.claim(
                conversation_id,
                identity_resolution.identity.owner_id,
                channel=_quality_channel(request),
            )
        run_id = str(uuid4())

        async def events():
            started_at = perf_counter()
            quality_turn = await _start_quality_turn(
                request,
                body,
                run_id=run_id,
                conversation_id=conversation_id,
                user_id=(
                    identity_resolution.identity.owner_id
                    if identity_resolution is not None
                    else None
                ),
            )
            terminal = False
            public_deltas: list[str] = []
            try:
                async for event in request.app.state.agent_service.stream_chat(
                    body.message,
                    conversation_id,
                    run_id=run_id,
                    user_id=(
                        identity_resolution.identity.owner_id
                        if identity_resolution is not None
                        else None
                    ),
                ):
                    data = event.get("data") if isinstance(event.get("data"), dict) else {}
                    if event["event"] == "text.delta":
                        public_deltas.append(str(data.get("delta") or ""))
                    if event["event"] in {"run.started", "run.completed", "approval.required"}:
                        data["quality_turn_id"] = getattr(quality_turn, "id", None)
                        data["feedback_token"] = getattr(quality_turn, "feedback_token", None)
                        event["data"] = data
                    if event["event"] in {"run.completed", "approval.required"}:
                        terminal = True
                        await _complete_quality_turn(
                            request,
                            run_id,
                            status=str(data.get("status") or "completed"),
                            duration_ms=(perf_counter() - started_at) * 1000,
                            response=data,
                        )
                        data.pop("quality_spans", None)
                    elif event["event"] == "run.error":
                        terminal = True
                        await _complete_quality_turn(
                            request,
                            run_id,
                            status="error",
                            duration_ms=(perf_counter() - started_at) * 1000,
                            answer="".join(public_deltas) or None,
                            error_type=str(
                                data.get("error_type")
                                or data.get("error")
                                or "AgentRunError"
                            ),
                        )
                    yield _sse(event)
            except asyncio.CancelledError:
                if not terminal:
                    await _complete_quality_turn(
                        request,
                        run_id,
                        status="cancelled",
                        duration_ms=(perf_counter() - started_at) * 1000,
                        answer="".join(public_deltas) or None,
                        error_type="CancelledError",
                    )
                raise
            except Exception as exc:
                if not terminal:
                    await _complete_quality_turn(
                        request,
                        run_id,
                        status="error",
                        duration_ms=(perf_counter() - started_at) * 1000,
                        answer="".join(public_deltas) or None,
                        error_type=type(exc).__name__,
                    )
                raise

        response = StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
        _apply_user_identity_cookies(response, request, identity_resolution)
        return response

    def _quality_repository(request: Request) -> QualityRepository:
        capture = getattr(request.app.state, "quality_capture_service", None)
        repository = getattr(capture, "repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="quality dataset unavailable")
        return repository

    @application.post(
        "/api/v1/quality/turns/{turn_id}/feedback",
        status_code=204,
    )
    async def quality_feedback(
        turn_id: str,
        body: QualityFeedbackRequest,
        request: Request,
    ):
        try:
            await _quality_repository(request).upsert_feedback(
                turn_id=turn_id,
                feedback_token=body.feedback_token,
                rating=body.rating,
                reason=body.reason,
                reason_code=body.reason_code,
                channel="web",
            )
        except InvalidFeedbackTokenError:
            raise HTTPException(status_code=403, detail="feedback token is invalid")
        except QualityNotFoundError:
            raise HTTPException(status_code=404, detail="quality turn not found")
        return Response(status_code=204)

    def _memory_resources(request: Request) -> tuple[MemoryRepository, MemoryService]:
        repository = getattr(request.app.state, "memory_repository", None)
        service = getattr(request.app.state, "memory_service", None)
        if repository is None or service is None:
            raise HTTPException(status_code=503, detail="long-term memory unavailable")
        return repository, service

    @application.get("/api/v1/memory")
    async def list_my_memory(request: Request):
        resolution = await _resolve_user_identity(request, "memory:read")
        if resolution is None:
            raise HTTPException(status_code=503, detail="user identity unavailable")
        user_id = resolution.identity.owner_id
        repository, _ = _memory_resources(request)
        response = JSONResponse(jsonable_encoder(await repository.list_memories(
            scope_type="user", owner_id=user_id, statuses=("confirmed",), limit=200,
        )))
        _apply_user_identity_cookies(response, request, resolution)
        return response

    @application.get("/api/v1/memory/candidates")
    async def list_my_memory_candidates(request: Request, response: Response):
        resolution = await _resolve_user_identity(request, "memory:read")
        if resolution is None:
            raise HTTPException(status_code=503, detail="user identity unavailable")
        repository, _ = _memory_resources(request)
        candidates = await repository.list_candidates(
            status="candidate",
            scope_type="user",
            owner_id=resolution.identity.owner_id,
            limit=200,
        )
        _apply_user_identity_cookies(response, request, resolution)
        payload = jsonable_encoder(candidates)
        eligible_types = {"user_preference", "user_context"}
        for item, candidate in zip(payload, candidates):
            eligible = candidate.memory_type in eligible_types
            item["auto_confirm_eligible"] = eligible
            item["auto_confirm_at"] = (
                candidate.created_at
                + timedelta(seconds=request.app.state.settings.memory_auto_confirm_seconds)
            ).isoformat() if eligible else None
        return payload

    @application.post("/api/v1/memory/candidates/{candidate_id}/confirm")
    async def confirm_my_memory_candidate(candidate_id: str, request: Request):
        resolution = await _resolve_user_identity(request, "memory:read")
        if resolution is None:
            raise HTTPException(status_code=503, detail="user identity unavailable")
        if resolution.identity.kind != "anonymous":
            try:
                request.app.state.user_auth_service.validate_user_csrf(
                    resolution.identity,
                    request.headers.get("X-User-CSRF-Token"),
                )
            except UserCsrfError as exc:
                raise HTTPException(status_code=403, detail="user CSRF token is invalid") from exc
        repository, service = _memory_resources(request)
        try:
            candidate = await repository.get_candidate(candidate_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="memory candidate not found") from exc
        if (
            candidate.status != "candidate"
            or candidate.scope_type != "user"
            or candidate.owner_id != resolution.identity.owner_id
        ):
            raise HTTPException(status_code=404, detail="memory candidate not found")
        return jsonable_encoder(
            await service.approve_candidate(
                candidate_id,
                actor=f"user:{resolution.identity.owner_id}",
            )
        )

    @application.post("/api/v1/memory/candidates/{candidate_id}/reject")
    async def reject_my_memory_candidate(candidate_id: str, request: Request):
        resolution = await _resolve_user_identity(request, "memory:read")
        if resolution is None:
            raise HTTPException(status_code=503, detail="user identity unavailable")
        if resolution.identity.kind != "anonymous":
            try:
                request.app.state.user_auth_service.validate_user_csrf(
                    resolution.identity,
                    request.headers.get("X-User-CSRF-Token"),
                )
            except UserCsrfError as exc:
                raise HTTPException(status_code=403, detail="user CSRF token is invalid") from exc
        repository, service = _memory_resources(request)
        try:
            candidate = await repository.get_candidate(candidate_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="memory candidate not found") from exc
        if (
            candidate.status != "candidate"
            or candidate.scope_type != "user"
            or candidate.owner_id != resolution.identity.owner_id
        ):
            raise HTTPException(status_code=404, detail="memory candidate not found")
        return jsonable_encoder(await service.reject_candidate(
            candidate_id,
            actor=f"user:{resolution.identity.owner_id}",
        ))

    @application.delete("/api/v1/memory/{memory_id}", status_code=204)
    async def forget_my_memory(memory_id: str, request: Request):
        resolution = await _resolve_user_identity(request, "memory:delete")
        if resolution is None:
            raise HTTPException(status_code=503, detail="user identity unavailable")
        user_id = resolution.identity.owner_id
        repository, service = _memory_resources(request)
        memory = await repository.get_memory(memory_id)
        if memory is None or memory.scope_type != "user" or memory.owner_id != user_id:
            raise HTTPException(status_code=404, detail="memory not found")
        await service.forget(memory_id, actor=f"user:{user_id}")
        return Response(status_code=204)

    @application.get("/api/v1/admin/memory/candidates")
    async def admin_memory_candidates(
        request: Request,
        status: str | None = None,
        scope_type: str | None = None,
        owner_id: str | None = None,
        domain_id: str | None = None,
        memory_type: str | None = None,
        limit: int = 100,
    ):
        await _require_admin_read(request)
        repository, _ = _memory_resources(request)
        return jsonable_encoder(await repository.list_candidates(
            status=status,
            scope_type="domain",
            owner_id=owner_id if scope_type == "domain" else None,
            domain_id=domain_id,
            memory_type=memory_type,
            limit=limit,
        ))

    @application.post("/api/v1/admin/memory/candidates/{candidate_id}/approve")
    async def admin_approve_memory(candidate_id: str, request: Request):
        await _require_admin_write(request)
        repository, service = _memory_resources(request)
        try:
            candidate = await repository.get_candidate(candidate_id)
            if candidate.scope_type != "domain":
                raise KeyError(candidate_id)
            return jsonable_encoder(await service.approve_candidate(candidate_id))
        except KeyError:
            raise HTTPException(status_code=404, detail="memory candidate not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @application.post("/api/v1/admin/memory/candidates/{candidate_id}/reject")
    async def admin_reject_memory(candidate_id: str, request: Request):
        await _require_admin_write(request)
        repository, service = _memory_resources(request)
        try:
            candidate = await repository.get_candidate(candidate_id)
            if candidate.scope_type != "domain":
                raise KeyError(candidate_id)
            return jsonable_encoder(await service.reject_candidate(candidate_id))
        except KeyError:
            raise HTTPException(status_code=404, detail="memory candidate not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @application.get("/api/v1/admin/memory")
    async def admin_list_memory(
        request: Request,
        scope_type: str | None = None,
        owner_id: str | None = None,
        domain_id: str | None = None,
        status: str = "confirmed",
        limit: int = 200,
    ):
        await _require_admin_read(request)
        repository, _ = _memory_resources(request)
        return jsonable_encoder(await repository.list_memories(
            scope_type="domain",
            owner_id=owner_id if scope_type == "domain" else None,
            domain_id=domain_id,
            statuses=(status,),
            limit=limit,
        ))

    @application.get("/api/v1/admin/memory/personal-statistics")
    async def admin_personal_memory_statistics(request: Request):
        await _require_admin_read(request)
        repository, _ = _memory_resources(request)
        return await repository.personal_memory_statistics()

    @application.post("/api/v1/admin/memory/promotions", status_code=201)
    async def admin_create_memory_promotion(
        body: DomainMemoryPromotionRequest, request: Request
    ):
        admin = await _require_admin_write(request)
        if not request.app.state.settings.memory_domain_promotion_enabled:
            raise HTTPException(status_code=503, detail="domain memory promotion is disabled")
        _, service = _memory_resources(request)
        try:
            return jsonable_encoder(await service.request_domain_promotion(
                source_memory_id=body.source_memory_id,
                target_domain_id=body.target_domain_id,
                public_summary=body.public_summary,
                requested_by=admin.username,
                valid_until=body.valid_until,
            ))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.get("/api/v1/admin/memory/promotions")
    async def admin_list_memory_promotions(request: Request, state: str = "pending"):
        await _require_admin_read(request)
        repository, _ = _memory_resources(request)
        return jsonable_encoder(await repository.list_domain_promotions(state))

    @application.post("/api/v1/admin/memory/promotions/{promotion_id}/approve")
    async def admin_approve_memory_promotion(promotion_id: str, request: Request):
        admin = await _require_admin_write(request)
        _, service = _memory_resources(request)
        try:
            return jsonable_encoder(await service.approve_domain_promotion(
                promotion_id, actor=admin.username
            ))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/v1/admin/memory/promotions/{promotion_id}/reject")
    async def admin_reject_memory_promotion(promotion_id: str, request: Request):
        admin = await _require_admin_write(request)
        _, service = _memory_resources(request)
        try:
            return jsonable_encoder(await service.reject_domain_promotion(
                promotion_id, actor=admin.username
            ))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.delete("/api/v1/admin/memory/{memory_id}", status_code=204)
    async def admin_delete_memory(memory_id: str, request: Request):
        await _require_admin_write(request)
        repository, service = _memory_resources(request)
        if await repository.get_memory(memory_id) is None:
            raise HTTPException(status_code=404, detail="memory not found")
        await service.forget(memory_id, actor="admin")
        return Response(status_code=204)

    @application.get("/api/v1/admin/quality/turns")
    async def admin_quality_turns(
        request: Request,
        page: int = 1,
        page_size: int | None = None,
        channel: str | None = None,
        status: str | None = None,
        rating: str | None = None,
        domain_id: str | None = None,
        user_id: str | None = None,
        query: str | None = None,
    ):
        await _require_admin_read(request)
        settings = request.app.state.settings
        result = await _quality_repository(request).list_turns(
            page=page,
            page_size=page_size or settings.agent_quality_page_size,
            channel=channel,
            status=status,
            rating=rating,
            domain_id=domain_id,
            user_id=user_id,
            query=query,
        )
        return jsonable_encoder(result)

    @application.get("/api/v1/admin/quality/analytics")
    async def admin_quality_analytics(
        request: Request,
        channel: str | None = None,
        domain_id: str | None = None,
        model_name: str | None = None,
        annotation_code: str | None = None,
    ):
        await _require_admin_read(request)
        return jsonable_encoder(
            await _quality_repository(request).get_analytics(
                channel=channel,
                domain_id=domain_id,
                model_name=model_name,
                annotation_code=annotation_code,
            )
        )

    @application.get("/api/v1/admin/quality/annotations")
    async def admin_quality_annotations(
        request: Request,
        page: int = 1,
        page_size: int = 50,
        code: str | None = None,
        review_status: str | None = None,
        source: str | None = None,
    ):
        await _require_admin_read(request)
        return jsonable_encoder(
            await _quality_repository(request).list_annotations(
                page=page,
                page_size=page_size,
                code=code,
                review_status=review_status,
                source=source,
            )
        )

    @application.patch("/api/v1/admin/quality/annotations/{annotation_id}")
    async def admin_review_quality_annotation(
        annotation_id: str,
        body: QualityAnnotationReviewRequest,
        request: Request,
    ):
        admin = await _require_admin_write(request)
        try:
            annotation = await _quality_repository(request).update_annotation_review(
                annotation_id,
                review_status=body.review_status,
                reviewer=admin.username,
            )
        except QualityNotFoundError:
            raise HTTPException(status_code=404, detail="quality annotation not found")
        return jsonable_encoder(annotation)

    @application.get("/api/v1/admin/quality/turns/{turn_id}")
    async def admin_quality_turn_detail(turn_id: str, request: Request):
        await _require_admin_read(request)
        turn = await _quality_repository(request).get_turn(turn_id)
        if turn is None:
            raise HTTPException(status_code=404, detail="quality turn not found")
        return jsonable_encoder(turn)

    @application.delete("/api/v1/admin/quality/turns/{turn_id}", status_code=204)
    async def admin_delete_quality_turn(turn_id: str, request: Request):
        admin = await _require_admin_write(request)
        repository = _quality_repository(request)
        if await repository.get_turn(turn_id) is None:
            raise HTTPException(status_code=404, detail="quality turn not found")
        await repository.delete_turn(turn_id)
        catalog = getattr(request.app.state, "catalog", None)
        if catalog is not None:
            await catalog.append_audit_event(
                actor=admin.username,
                action="quality.turn.delete",
                resource_type="quality_turn",
                resource_id=turn_id,
                details={},
            )
        return Response(status_code=204)

    @application.post(
        "/api/v1/admin/quality/turns/{turn_id}/eval-case",
        status_code=201,
    )
    async def admin_promote_quality_turn(
        turn_id: str,
        body: EvalCaseCreateRequest,
        request: Request,
    ):
        await _require_admin_write(request)
        repository = _quality_repository(request)
        turn = await repository.get_turn(turn_id)
        if turn is None:
            raise HTTPException(status_code=404, detail="quality turn not found")
        created = await repository.create_eval_case(
            EvalCaseCreate(
                source_turn_id=turn.id,
                name=body.name,
                question=turn.question,
                knowledge_space_id=turn.knowledge_space_id,
                domain_id=turn.domain_id,
                required_tools=body.required_tools,
                required_citation_types=body.required_citation_types,
                required_facts=body.required_facts,
                forbidden_facts=body.forbidden_facts,
                tags=body.tags,
                enabled=body.enabled,
                expected_behavior=body.expected_behavior,
                max_latency_ms=body.max_latency_ms,
                max_tool_calls=body.max_tool_calls,
                max_citations=body.max_citations,
                turns=body.turns,
                task_type=body.task_type,
                suite=body.suite,
                priority=body.priority,
                approval_state=body.approval_state,
            )
        )
        return jsonable_encoder(created)

    @application.get("/api/v1/admin/quality/eval-cases")
    async def admin_eval_cases(request: Request, enabled: bool | None = None):
        await _require_admin_read(request)
        return jsonable_encoder(
            await _quality_repository(request).list_eval_cases(enabled=enabled)
        )

    @application.put("/api/v1/admin/quality/eval-cases/{case_id}")
    async def admin_update_eval_case(
        case_id: str,
        body: EvalCaseUpdateRequest,
        request: Request,
    ):
        await _require_admin_write(request)
        repository = _quality_repository(request)
        existing = await repository.get_eval_case(case_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="evaluation case not found")
        updated = await repository.update_eval_case(
            case_id,
            EvalCaseCreate(
                source_turn_id=existing.source_turn_id,
                **body.model_dump(),
            ),
        )
        return jsonable_encoder(updated)

    @application.delete(
        "/api/v1/admin/quality/eval-cases/{case_id}", status_code=204
    )
    async def admin_delete_eval_case(case_id: str, request: Request):
        await _require_admin_write(request)
        repository = _quality_repository(request)
        if await repository.get_eval_case(case_id) is None:
            raise HTTPException(status_code=404, detail="evaluation case not found")
        await repository.delete_eval_case(case_id)
        return Response(status_code=204)

    @application.post("/api/v1/admin/quality/eval-runs", status_code=202)
    async def admin_run_evaluation(
        body: EvalRunCreateRequest,
        request: Request,
    ):
        await _require_admin_write(request)
        evaluator = getattr(request.app.state, "quality_evaluation_service", None)
        if evaluator is None:
            raise HTTPException(status_code=503, detail="quality evaluation unavailable")
        try:
            run = await evaluator.queue_cases(body.case_ids)
        except QualityNotFoundError:
            raise HTTPException(status_code=404, detail="evaluation case not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return jsonable_encoder(run)

    @application.post("/api/v1/admin/quality/eval-runs/{run_id}/cancel")
    async def admin_cancel_evaluation(run_id: str, request: Request):
        await _require_admin_write(request)
        evaluator = getattr(request.app.state, "quality_evaluation_service", None)
        if evaluator is None:
            raise HTTPException(status_code=503, detail="quality evaluation unavailable")
        try:
            return jsonable_encoder(await evaluator.cancel(run_id))
        except QualityNotFoundError:
            raise HTTPException(status_code=404, detail="evaluation run not found")

    @application.post(
        "/api/v1/admin/quality/eval-runs/{run_id}/retry-failed", status_code=202
    )
    async def admin_retry_failed_evaluation(run_id: str, request: Request):
        await _require_admin_write(request)
        evaluator = getattr(request.app.state, "quality_evaluation_service", None)
        if evaluator is None:
            raise HTTPException(status_code=503, detail="quality evaluation unavailable")
        try:
            return jsonable_encoder(await evaluator.retry_failed(run_id))
        except QualityNotFoundError:
            raise HTTPException(status_code=404, detail="evaluation run not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @application.get("/api/v1/admin/quality/eval-runs")
    async def admin_eval_runs(request: Request):
        await _require_admin_read(request)
        return jsonable_encoder(await _quality_repository(request).list_eval_runs())

    @application.get("/api/v1/admin/quality/eval-runs/{run_id}")
    async def admin_eval_run_detail(run_id: str, request: Request):
        await _require_admin_read(request)
        repository = _quality_repository(request)
        run = await repository.get_eval_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="evaluation run not found")
        return {
            "run": jsonable_encoder(run),
            "results": jsonable_encoder(await repository.list_eval_results(run_id)),
        }

    @application.get("/api/v1/admin/quality/export")
    async def admin_export_quality(
        request: Request,
        format: str = "jsonl",
        channel: str | None = None,
        status: str | None = None,
        rating: str | None = None,
        domain_id: str | None = None,
        user_id: str | None = None,
        query: str | None = None,
    ):
        await _require_admin_read(request)
        if format not in {"jsonl", "csv"}:
            raise HTTPException(status_code=422, detail="format must be jsonl or csv")
        repository = _quality_repository(request)

        async def exported_rows():
            page = 1
            if format == "csv":
                yield "run_id,channel,status,user_id,user_name,domain_id,question,answer,created_at\r\n"
            while True:
                result = await repository.list_turns(
                    page=page,
                    page_size=100,
                    channel=channel,
                    status=status,
                    rating=rating,
                    domain_id=domain_id,
                    user_id=user_id,
                    query=query,
                )
                if not result.items:
                    break
                for turn in result.items:
                    if format == "jsonl":
                        yield json.dumps(
                            jsonable_encoder(turn), ensure_ascii=False
                        ) + "\n"
                    else:
                        output = io.StringIO()
                        csv.writer(output).writerow(
                            [
                                turn.run_id,
                                turn.channel,
                                turn.status,
                                turn.user_id or "",
                                turn.user_name or "",
                                turn.domain_id or "",
                                turn.question,
                                turn.answer or "",
                                turn.created_at,
                            ]
                        )
                        yield output.getvalue()
                if page * result.page_size >= result.total:
                    break
                page += 1

        media_type = "application/x-ndjson" if format == "jsonl" else "text/csv"
        suffix = "jsonl" if format == "jsonl" else "csv"
        return StreamingResponse(
            exported_rows(),
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="agent-quality.{suffix}"'
            },
        )

    @application.get(
        "/api/v1/citations/detail",
        response_model=CitationDetailResponse,
    )
    async def citation_detail(
        source_type: str,
        source_id: str,
        request: Request,
        view: str = "section",
    ):
        if source_type not in {
            "code",
            "product_document",
            "knowledge_chunk",
            "swagger",
        }:
            raise HTTPException(status_code=422, detail="unsupported citation type")
        service = getattr(request.app.state, "citation_detail_service", None)
        if service is None:
            raise HTTPException(status_code=503, detail="citation detail unavailable")
        if view not in {"section", "full"}:
            raise HTTPException(status_code=422, detail="unsupported citation detail view")
        try:
            detail = await service.get(source_type, source_id, view=view)
        except CitationDetailNotFound:
            raise HTTPException(status_code=404, detail="citation detail not found")
        return CitationDetailResponse.model_validate(detail, from_attributes=True)

    @application.get("/api/v1/citations/document")
    async def citation_document(source_id: str, request: Request):
        service = getattr(request.app.state, "citation_detail_service", None)
        if service is None:
            raise HTTPException(status_code=503, detail="citation detail unavailable")
        try:
            path = await service.document_path(source_id)
        except CitationDetailNotFound:
            raise HTTPException(status_code=404, detail="citation document not found")
        return FileResponse(path, filename=path.name)

    @application.post(
        "/api/v1/agent/runs/{run_id}/decisions",
        response_model=AgentResponse,
    )
    async def decisions(run_id: str, body: DecisionsRequest, request: Request):
        await _resolve_user_identity(request, "agent:approve")
        result = await request.app.state.agent_service.decide(
            run_id,
            [item.model_dump() for item in body.decisions],
        )
        return AgentResponse.model_validate(result.to_dict())

    @application.post("/api/v1/agent/runs/{run_id}/decisions/stream")
    async def decisions_stream(run_id: str, body: DecisionsRequest, request: Request):
        await _resolve_user_identity(request, "agent:approve")
        await request.app.state.agent_service.require_pending(run_id)

        async def events():
            async for event in request.app.state.agent_service.stream_decide(
                run_id,
                [item.model_dump() for item in body.decisions],
            ):
                yield _sse(event)

        return StreamingResponse(events(), media_type="text/event-stream")

    def _history_service(request: Request) -> ConversationHistoryService:
        service = getattr(request.app.state, "conversation_history_service", None)
        if service is None:
            raise HTTPException(status_code=503, detail="conversation history unavailable")
        return service

    @application.get(
        "/api/v1/agent/conversations",
        response_model=ConversationHistoryPageResponse,
    )
    async def list_conversation_history(
        request: Request,
        response: Response,
        page: int = 1,
        page_size: int = 20,
        query: str = "",
    ):
        if page < 1 or page_size < 1 or page_size > 100 or len(query) > 200:
            raise HTTPException(status_code=422, detail="invalid history query")
        resolution = await _resolve_user_identity(request, "agent:query")
        if resolution is None:
            raise HTTPException(status_code=503, detail="user identity unavailable")
        result = await _history_service(request).list_conversations(
            resolution.identity.owner_id,
            page=page,
            page_size=page_size,
            query=query,
        )
        _apply_user_identity_cookies(response, request, resolution)
        return jsonable_encoder(result)

    @application.get(
        "/api/v1/agent/conversations/{conversation_id}",
        response_model=ConversationHistoryDetailResponse,
    )
    async def get_conversation_history(
        conversation_id: str, request: Request, response: Response
    ):
        resolution = await _resolve_user_identity(request, "agent:query")
        if resolution is None:
            raise HTTPException(status_code=503, detail="user identity unavailable")
        result = await _history_service(request).get_conversation(
            resolution.identity.owner_id, conversation_id
        )
        _apply_user_identity_cookies(response, request, resolution)
        return jsonable_encoder(result)

    @application.patch(
        "/api/v1/agent/conversations/{conversation_id}",
        response_model=ConversationHistoryItemResponse,
    )
    async def rename_conversation_history(
        conversation_id: str,
        body: ConversationRenameRequest,
        request: Request,
    ):
        resolution = await _resolve_user_identity(request, "agent:query")
        if resolution is None:
            raise HTTPException(status_code=503, detail="user identity unavailable")
        try:
            request.app.state.user_auth_service.validate_user_csrf(
                resolution.identity,
                request.headers.get("X-User-CSRF-Token"),
            )
        except UserCsrfError as exc:
            raise HTTPException(status_code=403, detail="user CSRF token is invalid") from exc
        result = await _history_service(request).rename_conversation(
            resolution.identity.owner_id, conversation_id, body.title
        )
        return jsonable_encoder(result)

    @application.delete(
        "/api/v1/agent/conversations/{conversation_id}",
        status_code=204,
    )
    async def delete_conversation(conversation_id: str, request: Request):
        resolution = await _resolve_user_identity(request, "agent:query")
        if resolution is not None:
            await request.app.state.user_auth_service.ownership.claim(
                conversation_id,
                resolution.identity.owner_id,
                channel=_quality_channel(request),
            )
        await request.app.state.agent_service.delete_conversation(conversation_id)
        if resolution is not None:
            await request.app.state.user_auth_service.ownership.release(
                conversation_id, resolution.identity.owner_id
            )
        return Response(status_code=204)

    @application.get("/api/v1/knowledge/spaces")
    async def knowledge_spaces(request: Request):
        catalog = getattr(request.app.state, "catalog", None)
        if catalog is None:
            raise HTTPException(status_code=503, detail="knowledge catalog unavailable")
        result = []
        for space in await catalog.list_spaces():
            domains = await catalog.list_domains(space.id)
            result.append(
                {
                    "id": space.id,
                    "name": space.name,
                    "domains": [
                        {"id": item.id, "name": item.name, "sort_order": item.sort_order}
                        for item in domains
                    ],
                }
            )
        return result

    @application.post("/api/v1/admin/auth/login")
    async def admin_login(body: AdminLoginRequest, request: Request):
        authenticator = getattr(request.app.state, "admin_authenticator", None)
        settings, sessions = _admin_resources(request)
        if authenticator is None or not authenticator.authenticate(
            body.username, body.password
        ):
            raise HTTPException(status_code=401, detail="invalid administrator credentials")
        credentials = await sessions.create(body.username)
        response = JSONResponse(
            {
                "username": body.username,
                "csrf_token": credentials.csrf_token,
                "expires_at": credentials.expires_at.isoformat(),
            }
        )
        response.set_cookie(
            settings.admin_cookie_name,
            credentials.token,
            max_age=settings.admin_session_ttl_seconds,
            httponly=True,
            secure=settings.admin_cookie_secure,
            samesite="strict",
            path="/",
        )
        return response

    @application.get("/api/v1/admin/auth/me")
    async def admin_me(request: Request):
        admin = await _require_admin_read(request)
        settings, sessions = _admin_resources(request)
        token = request.cookies[settings.admin_cookie_name]
        return {
            "username": admin.username,
            "csrf_token": await sessions.get_csrf_token(token),
            "expires_at": admin.expires_at,
        }

    @application.post("/api/v1/admin/auth/logout", status_code=204)
    async def admin_logout(request: Request):
        settings, sessions = _admin_resources(request)
        token = request.cookies.get(settings.admin_cookie_name, "")
        if not token:
            raise InvalidAdminSessionError("admin session is missing")
        await sessions.logout(
            token,
            csrf_token=request.headers.get("X-CSRF-Token"),
        )
        response = Response(status_code=204)
        response.delete_cookie(settings.admin_cookie_name, path="/")
        return response

    @application.get("/api/v1/admin/sources")
    async def admin_sources(request: Request):
        await _require_admin_read(request)
        catalog = request.app.state.catalog
        sources = await catalog.list_sources(space_id="middle-platform")
        return jsonable_encoder(
            [
                source
                for source in sources
                if source.config.get("lifecycle_state") != "deleted"
            ]
        )

    @application.get("/api/v1/admin/sources/{source_id}")
    async def admin_source_detail(source_id: str, request: Request):
        await _require_admin_read(request)
        catalog = request.app.state.catalog
        source = await catalog.get_source(source_id)
        if source is None or source.config.get("lifecycle_state") == "deleted":
            raise HTTPException(status_code=404, detail="knowledge source not found")
        return {
            "source": jsonable_encoder(source),
            "rules": jsonable_encoder(await catalog.list_domain_rules(source_id)),
            "versions": jsonable_encoder(await catalog.list_versions(source_id)),
        }

    @application.put("/api/v1/admin/sources/{source_id}/rules")
    async def admin_replace_source_rules(
        source_id: str,
        body: DomainRulesReplaceRequest,
        request: Request,
    ):
        admin = await _require_admin_write(request)
        catalog = request.app.state.catalog
        valid_domains = {
            item.id for item in await catalog.list_domains("middle-platform")
        }
        if any(rule.domain_id not in valid_domains for rule in body.rules):
            raise HTTPException(status_code=422, detail="unknown knowledge domain")
        try:
            rules = await catalog.replace_domain_rules(
                source_id,
                [
                    SourceDomainRuleCreate(
                        id=str(uuid4()),
                        source_id=source_id,
                        pattern=rule.pattern,
                        target_domain_id=rule.domain_id,
                        priority=rule.priority,
                    )
                    for rule in body.rules
                ],
            )
        except CatalogNotFoundError:
            raise HTTPException(status_code=404, detail="knowledge source not found") from None
        except CatalogConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        await catalog.append_audit_event(
            actor=admin.username,
            action="source.rules.replace",
            resource_type="knowledge_source",
            resource_id=source_id,
            details={"rule_count": len(rules)},
        )
        return jsonable_encoder(rules)

    @application.post("/api/v1/admin/sources/{source_id}/sync", status_code=202)
    async def admin_sync_source(source_id: str, request: Request):
        admin = await _require_admin_write(request)
        catalog = request.app.state.catalog
        source = await catalog.get_source(source_id)
        if source is None or not source.enabled:
            raise HTTPException(status_code=404, detail="knowledge source not found")
        if source.source_type is not SourceType.GIT:
            raise HTTPException(
                status_code=409,
                detail="only Git sources support manual sync",
            )
        existing = next(
            (
                job
                for job in await catalog.list_jobs(source_id=source_id)
                if job.kind == "manual"
                and job.state in {SyncJobState.QUEUED, SyncJobState.RUNNING}
            ),
            None,
        )
        if existing is not None:
            return jsonable_encoder(existing)
        job = await catalog.enqueue_job(source_id=source_id, kind="manual")
        await catalog.append_audit_event(
            actor=admin.username,
            action="source.sync",
            resource_type="knowledge_source",
            resource_id=source_id,
            details={"job_id": job.id},
        )
        return jsonable_encoder(job)

    @application.delete("/api/v1/admin/sources/{source_id}", status_code=202)
    async def admin_delete_source(
        source_id: str,
        body: SourceDeleteRequest,
        request: Request,
    ):
        admin = await _require_admin_write(request)
        catalog = request.app.state.catalog
        source = await catalog.get_source(source_id)
        if source is None or source.config.get("lifecycle_state") == "deleted":
            raise HTTPException(status_code=404, detail="knowledge source not found")
        if not hmac.compare_digest(
            body.confirm_name.encode("utf-8"), source.name.encode("utf-8")
        ):
            raise HTTPException(status_code=409, detail="source name confirmation mismatch")
        existing = next(
            (
                job
                for job in await catalog.list_jobs(source_id=source_id)
                if job.kind == "delete"
                and job.state in {SyncJobState.QUEUED, SyncJobState.RUNNING}
            ),
            None,
        )
        if existing is not None:
            return jsonable_encoder(existing)
        await catalog.update_source(
            source_id,
            enabled=False,
            config={**source.config, "lifecycle_state": "deleting"},
        )
        job = await catalog.enqueue_job(source_id=source_id, kind="delete")
        await catalog.append_audit_event(
            actor=admin.username,
            action="source.delete",
            resource_type="knowledge_source",
            resource_id=source_id,
            details={"job_id": job.id},
        )
        return jsonable_encoder(job)

    @application.get("/api/v1/admin/jobs")
    async def admin_jobs(request: Request, source_id: str | None = None):
        await _require_admin_read(request)
        return jsonable_encoder(
            await request.app.state.catalog.list_jobs(source_id=source_id)
        )

    @application.post("/api/v1/admin/jobs/{job_id}/retry", status_code=202)
    async def admin_retry_job(job_id: str, request: Request):
        admin = await _require_admin_write(request)
        catalog = request.app.state.catalog
        try:
            job = await catalog.get_job(job_id)
        except CatalogNotFoundError:
            raise HTTPException(status_code=404, detail="sync job not found") from None
        if job.state is not SyncJobState.FAILED:
            raise HTTPException(status_code=409, detail="only failed jobs can be retried")
        try:
            retried = await catalog.requeue_job(
                job_id,
                available_at=datetime.now(UTC),
            )
        except CatalogConflictError:
            raise HTTPException(status_code=409, detail="sync job state changed") from None
        await catalog.append_audit_event(
            actor=admin.username,
            action="job.retry",
            resource_type="sync_job",
            resource_id=job_id,
            details={},
        )
        return jsonable_encoder(retried)

    @application.get("/api/v1/admin/gitlab/projects")
    async def admin_gitlab_projects(search: str, request: Request):
        await _require_admin_read(request)
        client = getattr(request.app.state, "gitlab_client", None)
        if client is None:
            raise HTTPException(status_code=503, detail="GitLab integration unavailable")
        return jsonable_encoder(await client.search_projects(search))

    @application.get("/api/v1/admin/gitlab/projects/{project_id}/branches")
    async def admin_gitlab_branches(
        project_id: str, request: Request, search: str = ""
    ):
        await _require_admin_read(request)
        client = getattr(request.app.state, "gitlab_client", None)
        if client is None:
            raise HTTPException(status_code=503, detail="GitLab integration unavailable")
        return jsonable_encoder(await client.list_branches(project_id, search))

    @application.post("/api/v1/admin/sources/git", status_code=201)
    async def admin_create_git_source(
        body: GitSourceCreateRequest, request: Request
    ):
        admin = await _require_admin_write(request)
        catalog = request.app.state.catalog
        _validate_clean_http_url(body.project_url, "project URL")
        _validate_clean_http_url(body.project_web_url, "project web URL")
        valid_domains = {
            item.id for item in await catalog.list_domains("middle-platform")
        }
        if any(rule.domain_id not in valid_domains for rule in body.rules):
            raise HTTPException(status_code=422, detail="unknown knowledge domain")

        source_id = str(uuid4())
        source = await catalog.create_source(
            KnowledgeSourceCreate(
                id=source_id,
                space_id="middle-platform",
                domain_id=None,
                source_type=SourceType.GIT,
                name=body.name.strip(),
                config={
                    "project_id": body.project_id,
                    "project_path": body.project_path,
                    "project_url": body.project_url,
                    "project_web_url": body.project_web_url,
                    "branch": body.branch,
                },
            )
        )
        try:
            for rule in body.rules:
                await catalog.create_domain_rule(
                    SourceDomainRuleCreate(
                        id=str(uuid4()),
                        source_id=source_id,
                        pattern=rule.pattern,
                        target_domain_id=rule.domain_id,
                        priority=rule.priority,
                    )
                )
            webhook_secret = secrets.token_urlsafe(32)
            await catalog.set_webhook_secret_hash(
                source_id,
                hashlib.sha256(webhook_secret.encode("utf-8")).hexdigest(),
            )
        except Exception:
            await catalog.delete_source(source_id)
            raise
        await catalog.append_audit_event(
            actor=admin.username,
            action="source.create",
            resource_type="knowledge_source",
            resource_id=source_id,
            details={"source_type": "git"},
        )
        return {
            "source": jsonable_encoder(await catalog.get_source(source_id)),
            "webhook_secret": webhook_secret,
        }

    @application.post("/api/v1/admin/sources/swagger", status_code=201)
    async def admin_create_swagger_source(
        body: SwaggerSourceCreateRequest, request: Request
    ):
        admin = await _require_admin_write(request)
        catalog = request.app.state.catalog
        settings = request.app.state.settings
        parsed = urlsplit(body.url.strip())
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or host not in settings.swagger_allowed_host_set
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise HTTPException(status_code=422, detail="Swagger URL host is not allowed")
        valid_domains = {
            item.id for item in await catalog.list_domains("middle-platform")
        }
        if body.domain_id not in valid_domains:
            raise HTTPException(status_code=422, detail="unknown knowledge domain")
        if body.auth_type == "bearer" and not body.bearer_token:
            raise HTTPException(status_code=422, detail="Bearer token is required")
        if body.auth_type == "basic" and (not body.username or not body.password):
            raise HTTPException(status_code=422, detail="Basic credentials are required")
        secret_store = getattr(request.app.state, "catalog_secret_store", None)
        if body.auth_type != "none" and secret_store is None:
            raise HTTPException(status_code=503, detail="secret storage unavailable")
        source_id = str(uuid4())
        await catalog.create_source(
            KnowledgeSourceCreate(
                id=source_id,
                space_id="middle-platform",
                domain_id=body.domain_id,
                source_type=SourceType.SWAGGER,
                name=body.name.strip(),
                config={
                    "url": body.url.strip(),
                    "auth_type": body.auth_type,
                    "timeout_seconds": body.timeout_seconds,
                },
            )
        )
        try:
            if body.auth_type == "bearer":
                await secret_store.set(source_id, "bearer_token", body.bearer_token)
            elif body.auth_type == "basic":
                await secret_store.set(source_id, "username", body.username)
                await secret_store.set(source_id, "password", body.password)
        except Exception:
            await catalog.delete_source(source_id)
            raise
        await catalog.append_audit_event(
            actor=admin.username,
            action="source.create",
            resource_type="knowledge_source",
            resource_id=source_id,
            details={"source_type": "swagger"},
        )
        return jsonable_encoder(await catalog.get_source(source_id))

    @application.post("/api/v1/admin/sources/documents", status_code=202)
    async def admin_create_document_source(
        request: Request,
        name: str = Form(...),
        domain_id: str = Form(...),
        version: str = Form(...),
        upload: UploadFile = File(...),
    ):
        admin = await _require_admin_write(request)
        catalog = request.app.state.catalog
        settings = request.app.state.settings
        if not name.strip():
            raise HTTPException(status_code=422, detail="source name is required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", version):
            raise HTTPException(status_code=422, detail="document version is invalid")
        valid_domains = {
            item.id for item in await catalog.list_domains("middle-platform")
        }
        if domain_id not in valid_domains:
            raise HTTPException(status_code=422, detail="unknown knowledge domain")

        source_id = str(uuid4())
        uploads_root = settings.resolved_knowledge_storage_root / "uploads"
        staging_root = uploads_root / ".staging"
        destination = uploads_root / source_id / version
        staging_root.mkdir(parents=True, exist_ok=True)
        staging_path = staging_root / f"{source_id}.upload"
        total = 0
        try:
            with staging_path.open("wb") as output:
                while block := await upload.read(1024 * 1024):
                    total += len(block)
                    if total > settings.upload_max_file_bytes:
                        raise HTTPException(status_code=413, detail="upload file is too large")
                    output.write(block)
            filename = Path(upload.filename or "upload").name
            if filename.lower().endswith(".zip"):
                extracted = extract_upload_archive(
                    staging_path,
                    destination,
                    settings.upload_max_files,
                    settings.upload_max_batch_bytes,
                    max_file_bytes=settings.upload_max_file_bytes,
                )
                if not extracted:
                    raise UnsafeArchiveError(
                        "archive contains no supported document files"
                    )
            else:
                if Path(filename).suffix.lower() not in {".md", ".txt", ".docx", ".pdf"}:
                    raise HTTPException(
                        status_code=422, detail="unsupported document file type"
                    )
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(staging_path, destination / filename)
            source = await catalog.create_source(
                KnowledgeSourceCreate(
                    id=source_id,
                    space_id="middle-platform",
                    domain_id=domain_id,
                    source_type=SourceType.DOCUMENT,
                    name=name.strip(),
                    config={
                        "pending_upload_path": str(destination),
                        "pending_version": version,
                    },
                )
            )
            job = await catalog.enqueue_job(source_id=source_id, kind="document")
            await catalog.append_audit_event(
                actor=admin.username,
                action="source.create",
                resource_type="knowledge_source",
                resource_id=source_id,
                details={"source_type": "document", "job_id": job.id},
            )
            return {"source": jsonable_encoder(source), "job": jsonable_encoder(job)}
        except (UnsafeArchiveError, zipfile.BadZipFile) as exc:
            shutil.rmtree(destination.parent, ignore_errors=True)
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception:
            try:
                await catalog.delete_source(source_id)
            finally:
                shutil.rmtree(destination.parent, ignore_errors=True)
            raise
        finally:
            staging_path.unlink(missing_ok=True)

    @application.post(
        "/api/v1/admin/sources/{source_id}/documents/versions",
        status_code=202,
    )
    async def admin_upload_document_version(
        source_id: str,
        request: Request,
        version: str = Form(...),
        upload: UploadFile = File(...),
    ):
        admin = await _require_admin_write(request)
        catalog = request.app.state.catalog
        settings = request.app.state.settings
        source = await catalog.get_source(source_id)
        if (
            source is None
            or not source.enabled
            or source.source_type is not SourceType.DOCUMENT
        ):
            raise HTTPException(status_code=404, detail="document source not found")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", version):
            raise HTTPException(status_code=422, detail="document version is invalid")
        uploads_root = settings.resolved_knowledge_storage_root / "uploads"
        destination = uploads_root / source_id / version
        if destination.exists():
            raise HTTPException(status_code=409, detail="document version already exists")
        if any(
            job.kind == "document"
            and job.state in {SyncJobState.QUEUED, SyncJobState.RUNNING}
            for job in await catalog.list_jobs(source_id=source_id)
        ):
            raise HTTPException(
                status_code=409,
                detail="a document version is already being processed",
            )
        staging_root = uploads_root / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging_path = staging_root / f"{source_id}-{uuid4()}.upload"
        completed = False
        source_config_updated = False
        queued_job = None
        try:
            total = 0
            with staging_path.open("wb") as output:
                while block := await upload.read(1024 * 1024):
                    total += len(block)
                    if total > settings.upload_max_file_bytes:
                        raise HTTPException(status_code=413, detail="upload file is too large")
                    output.write(block)
            filename = Path(upload.filename or "upload").name
            if filename.lower().endswith(".zip"):
                extracted = extract_upload_archive(
                    staging_path,
                    destination,
                    settings.upload_max_files,
                    settings.upload_max_batch_bytes,
                    max_file_bytes=settings.upload_max_file_bytes,
                )
                if not extracted:
                    raise UnsafeArchiveError(
                        "archive contains no supported document files"
                    )
            else:
                if Path(filename).suffix.lower() not in {".md", ".txt", ".docx", ".pdf"}:
                    raise HTTPException(
                        status_code=422, detail="unsupported document file type"
                    )
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(staging_path, destination / filename)
            updated = await catalog.update_source(
                source_id,
                config={
                    **source.config,
                    "pending_upload_path": str(destination),
                    "pending_version": version,
                },
            )
            source_config_updated = True
            job = await catalog.enqueue_job(source_id=source_id, kind="document")
            queued_job = job
            await catalog.append_audit_event(
                actor=admin.username,
                action="source.version.upload",
                resource_type="knowledge_source",
                resource_id=source_id,
                details={"version": version, "job_id": job.id},
            )
            completed = True
            return {"source": jsonable_encoder(updated), "job": jsonable_encoder(job)}
        except (UnsafeArchiveError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception:
            if queued_job is not None:
                await catalog.delete_queued_job(queued_job.id)
            if source_config_updated:
                await catalog.update_source(source_id, config=source.config)
            raise
        finally:
            staging_path.unlink(missing_ok=True)
            if not completed:
                shutil.rmtree(destination, ignore_errors=True)

    @application.post("/api/v1/webhooks/gitlab/{source_id}", status_code=202)
    async def gitlab_webhook(source_id: str, request: Request):
        catalog = getattr(request.app.state, "catalog", None)
        secret_store = getattr(request.app.state, "catalog_secret_store", None)
        if catalog is None:
            raise HTTPException(status_code=503, detail="webhook integration unavailable")
        source = await catalog.get_source(source_id)
        if source is None or source.source_type is not SourceType.GIT or not source.enabled:
            raise HTTPException(status_code=404, detail="Git source not found")
        provided_secret = request.headers.get("X-Gitlab-Token", "")
        stored_hash = await catalog.get_webhook_secret_hash(source_id)
        if stored_hash is None and secret_store is not None:
            stored_hash = await secret_store.get(source_id, "webhook_secret_hash")
        provided_hash = hashlib.sha256(provided_secret.encode("utf-8")).hexdigest()
        if not stored_hash or not hmac.compare_digest(
            stored_hash.encode("ascii"), provided_hash.encode("ascii")
        ):
            raise HTTPException(status_code=403, detail="invalid webhook secret")
        payload = await request.json()
        if str((payload.get("project") or {}).get("id")) != str(
            source.config.get("project_id")
        ):
            raise HTTPException(status_code=400, detail="webhook project mismatch")
        expected_ref = f"refs/heads/{source.config.get('branch')}"
        if payload.get("ref") != expected_ref:
            raise HTTPException(status_code=400, detail="webhook branch mismatch")
        target_commit = str(payload.get("after") or "").strip()
        if not target_commit or set(target_commit) == {"0"}:
            raise HTTPException(status_code=400, detail="webhook commit is missing")
        job = await catalog.enqueue_job(
            source_id=source_id,
            kind="webhook",
            target_commit=target_commit,
        )
        return jsonable_encoder(job)

    def _validate_clean_http_url(value: str, label: str) -> None:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise HTTPException(status_code=422, detail=f"{label} is invalid")

    @application.get("/health/live")
    async def live():
        return {"status": "live"}

    @application.get("/health/ready")
    async def ready(request: Request):
        components = {
            name: dict(details)
            for name, details in request.app.state.component_status.items()
        }
        bridge = getattr(request.app.state, "feishu_bot_bridge", None)
        gateway = getattr(bridge, "gateway", None)
        connected = getattr(gateway, "connected", None)
        if (
            components.get("feishu_bot", {}).get("status") == "available"
            and connected is False
        ):
            components["feishu_bot"] = {"status": "unavailable"}
        critical = ("model", "sqlite", "chroma")
        critical_ready = all(
            components.get(name, {}).get("status") == "available" for name in critical
        )
        mcp_ready = components.get("mcp", {}).get("status") == "available"
        status = "ready" if critical_ready and mcp_ready else "degraded"
        return {"status": status, "components": components}

    frontend_settings = injected_settings or Settings()
    frontend_dist = frontend_settings.resolved_frontend_dist
    frontend_index = frontend_dist / "index.html"
    if frontend_index.is_file():

        @application.get("/assets/{asset_path:path}", include_in_schema=False)
        async def frontend_asset(asset_path: str):
            target = (frontend_dist / "assets" / asset_path).resolve()
            assets_root = (frontend_dist / "assets").resolve()
            if assets_root not in target.parents or not target.is_file():
                raise HTTPException(status_code=404, detail="asset not found")
            return FileResponse(target)

        @application.get("/{spa_path:path}", include_in_schema=False)
        async def frontend_spa(spa_path: str):
            first_segment = spa_path.split("/", 1)[0]
            if first_segment in {"api", "health", "docs", "redoc"} or spa_path == (
                "openapi.json"
            ):
                raise HTTPException(status_code=404, detail="not found")
            candidate = (frontend_dist / spa_path).resolve()
            if frontend_dist.resolve() in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend_index, media_type="text/html")

    return application


app = create_app()
