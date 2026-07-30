from functools import lru_cache
from pathlib import Path
import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_VALUES = {"", "<fill-locally>", "你的阿里云百炼APIKey"}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
VALID_AGENT_PROVIDERS = {"openai", "deepseek"}
VALID_DATA_STORE_PROVIDERS = {"sqlite", "postgres"}
VALID_VECTOR_STORE_PROVIDERS = {"chroma", "pgvector"}
VALID_DATABASE_SSL_MODES = {
    "disable",
    "allow",
    "prefer",
    "require",
    "verify-ca",
    "verify-full",
}
VALID_BUG_GRAPH_CHECKPOINT_PROVIDERS = {"auto", "sqlite", "postgres"}
POSTGRES_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _usable(value: str) -> str:
    normalized = value.strip()
    return "" if normalized in PLACEHOLDER_VALUES else normalized


class Settings(BaseSettings):
    """Runtime configuration loaded from .env and environment variables."""

    embedding_api_key: str = Field(default="", alias="EMBEDDING_API_KEY")
    dashscope_api_key: str = Field(default="", alias="DASHSCOPE_API_KEY")
    embedding_base_url: str = Field(default="", alias="EMBEDDING_BASE_URL")
    embedding_model: str = Field(default="text-embedding-v4", alias="EMBEDDING_MODEL")
    embedding_dimensions: int | None = Field(default=1024, alias="EMBEDDING_DIMENSIONS")
    embedding_batch_size: int = Field(default=10, alias="EMBEDDING_BATCH_SIZE")

    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_chat_model: str = Field(default="deepseek-v4-flash", alias="DEEPSEEK_CHAT_MODEL")
    deepseek_reasoning_model: str = Field(
        default="deepseek-v4-pro",
        alias="DEEPSEEK_REASONING_MODEL",
    )
    deepseek_reasoning_enabled: bool = Field(
        default=True,
        alias="DEEPSEEK_REASONING_ENABLED",
    )
    query_rewrite_enabled: bool = Field(default=True, alias="QUERY_REWRITE_ENABLED")
    query_rewrite_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        alias="QUERY_REWRITE_TIMEOUT_SECONDS",
    )

    rerank_api_key: str = Field(default="", alias="RERANK_API_KEY")
    rerank_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-api/v1",
        alias="RERANK_BASE_URL",
    )
    rerank_model: str = Field(default="qwen3-rerank", alias="RERANK_MODEL")
    rerank_enabled: bool = Field(default=True, alias="RERANK_ENABLED")
    rerank_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        alias="RERANK_TIMEOUT_SECONDS",
    )
    keyword_candidate_k: int = Field(default=20, ge=1, alias="KEYWORD_CANDIDATE_K")
    vector_candidate_k: int = Field(default=20, ge=1, alias="VECTOR_CANDIDATE_K")
    final_result_k: int = Field(default=5, ge=1, alias="FINAL_RESULT_K")
    retrieval_warmup_enabled: bool = Field(
        default=True,
        alias="RETRIEVAL_WARMUP_ENABLED",
    )
    bm25_memory_filter_enabled: bool = Field(
        default=True,
        alias="BM25_MEMORY_FILTER_ENABLED",
    )
    bm25_stale_while_refresh_enabled: bool = Field(
        default=True,
        alias="BM25_STALE_WHILE_REFRESH_ENABLED",
    )
    retrieval_parallel_routes_enabled: bool = Field(
        default=True,
        alias="RETRIEVAL_PARALLEL_ROUTES_ENABLED",
    )

    legacy_openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    legacy_openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")

    knowledge_source_path: Path = Field(
        default=Path(r"D:\javaProgram\metric-platform-knowledge.md"),
        alias="KNOWLEDGE_SOURCE_PATH",
    )
    vector_store_path: Path = Field(
        default=Path(r"D:\javaProgram\its-middle-platform-agent-rag\storage\chroma"),
        alias="VECTOR_STORE_PATH",
    )
    chroma_collection_name: str = Field(
        default="metric_platform_knowledge",
        alias="CHROMA_COLLECTION_NAME",
    )

    data_store_provider: str = Field(
        default="sqlite",
        alias="DATA_STORE_PROVIDER",
    )
    database_url: str = Field(default="", alias="DATABASE_URL", repr=False)
    pghost: str = Field(default="", alias="PGHOST")
    pgport: int = Field(default=5432, gt=0, le=65535, alias="PGPORT")
    pgdatabase: str = Field(default="", alias="PGDATABASE")
    pguser: str = Field(default="", alias="PGUSER")
    pgpassword: str = Field(default="", alias="PGPASSWORD", repr=False)
    database_schema: str = Field(default="public", alias="DATABASE_SCHEMA")
    database_ssl_mode: str = Field(default="prefer", alias="DATABASE_SSL_MODE")
    database_pool_size: int = Field(
        default=5,
        ge=1,
        le=100,
        alias="DATABASE_POOL_SIZE",
    )
    database_max_overflow: int = Field(
        default=5,
        ge=0,
        le=200,
        alias="DATABASE_MAX_OVERFLOW",
    )
    database_pool_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=300,
        alias="DATABASE_POOL_TIMEOUT_SECONDS",
    )
    database_pool_recycle_seconds: int = Field(
        default=1800,
        gt=0,
        le=86400,
        alias="DATABASE_POOL_RECYCLE_SECONDS",
    )
    pgvector_pool_max_idle_seconds: float = Field(
        default=300.0,
        gt=0,
        le=86400,
        alias="PGVECTOR_POOL_MAX_IDLE_SECONDS",
    )
    database_statement_timeout_seconds: int = Field(
        default=30,
        gt=0,
        le=3600,
        alias="DATABASE_STATEMENT_TIMEOUT_SECONDS",
    )
    database_migration_batch_size: int = Field(
        default=5000,
        ge=1,
        le=50000,
        alias="DATABASE_MIGRATION_BATCH_SIZE",
    )
    vector_store_provider: str = Field(
        default="chroma",
        alias="VECTOR_STORE_PROVIDER",
    )
    vector_shadow_enabled: bool = Field(
        default=False,
        alias="VECTOR_SHADOW_ENABLED",
    )
    vector_shadow_sample_rate: float = Field(
        default=1.0,
        gt=0,
        le=1,
        alias="VECTOR_SHADOW_SAMPLE_RATE",
    )
    pgvector_schema: str = Field(default="public", alias="PGVECTOR_SCHEMA")
    pgvector_table: str = Field(
        default="vector_entries",
        alias="PGVECTOR_TABLE",
    )
    pgvector_batch_size: int = Field(
        default=500,
        ge=1,
        le=5000,
        alias="PGVECTOR_BATCH_SIZE",
    )
    pgvector_hnsw_ef_search: int = Field(
        default=100,
        ge=1,
        le=1000,
        alias="PGVECTOR_HNSW_EF_SEARCH",
    )
    pgvector_dimensions: int = Field(
        default=1024,
        ge=1,
        alias="PGVECTOR_DIMENSIONS",
    )

    chunk_max_chars: int = Field(default=1800, alias="CHUNK_MAX_CHARS")
    chunk_overlap_chars: int = Field(default=180, alias="CHUNK_OVERLAP_CHARS")
    bm25_title_weight: float = Field(default=0.65, ge=0, alias="BM25_TITLE_WEIGHT")
    bm25_keywords_weight: float = Field(
        default=0.35,
        ge=0,
        alias="BM25_KEYWORDS_WEIGHT",
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_file: Path = Field(
        default=Path("logs/knowledge-rag.log"),
        alias="LOG_FILE",
    )
    log_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        gt=0,
        alias="LOG_MAX_BYTES",
    )
    log_backup_count: int = Field(default=5, ge=0, alias="LOG_BACKUP_COUNT")

    agent_model_provider: str = Field(default="openai", alias="AGENT_MODEL_PROVIDER")
    agent_model_name: str = Field(default="gpt-5.4-mini", alias="AGENT_MODEL_NAME")
    agent_openai_api_key: str = Field(default="", alias="AGENT_OPENAI_API_KEY")
    agent_openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        alias="AGENT_OPENAI_BASE_URL",
    )
    agent_max_turns: int = Field(default=12, ge=1, alias="AGENT_MAX_TURNS")
    agent_manager_reasoning_enabled: bool = Field(
        default=True,
        alias="AGENT_MANAGER_REASONING_ENABLED",
    )
    agent_manager_reasoning_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=180,
        alias="AGENT_MANAGER_REASONING_TIMEOUT_SECONDS",
    )
    agent_intent_router_enabled: bool = Field(
        default=True,
        alias="AGENT_INTENT_ROUTER_ENABLED",
    )
    agent_llm_router_enabled: bool = Field(
        default=True,
        alias="AGENT_LLM_ROUTER_ENABLED",
    )
    agent_intent_router_min_confidence: float = Field(
        default=0.75,
        ge=0,
        le=1,
        alias="AGENT_INTENT_ROUTER_MIN_CONFIDENCE",
    )
    agent_direct_specialist_enabled: bool = Field(
        default=True, alias="AGENT_DIRECT_SPECIALIST_ENABLED"
    )
    agent_composite_evidence_enabled: bool = Field(
        default=True, alias="AGENT_COMPOSITE_EVIDENCE_ENABLED"
    )
    agent_retrieval_max_calls: int = Field(
        default=3,
        ge=1,
        alias="AGENT_RETRIEVAL_MAX_CALLS",
    )
    agent_retrieval_max_identical_queries: int = Field(
        default=1,
        ge=1,
        alias="AGENT_RETRIEVAL_MAX_IDENTICAL_QUERIES",
    )
    agent_retrieval_timeout_seconds: float = Field(
        default=20,
        gt=0,
        le=120,
        alias="AGENT_RETRIEVAL_TIMEOUT_SECONDS",
    )
    agent_public_citation_limit: int = Field(
        default=10,
        ge=1,
        le=50,
        alias="AGENT_PUBLIC_CITATION_LIMIT",
    )
    agent_session_db: Path = Field(
        default=Path("storage/agent_sessions.db"),
        alias="AGENT_SESSION_DB",
    )
    agent_session_history_limit: int = Field(
        default=50,
        ge=1,
        alias="AGENT_SESSION_HISTORY_LIMIT",
    )
    memory_enabled: bool = Field(default=True, alias="MEMORY_ENABLED")
    memory_db: Path = Field(
        default=Path("storage/agent_memory.db"), alias="MEMORY_DB"
    )
    memory_max_recall: int = Field(default=5, ge=1, le=20, alias="MEMORY_MAX_RECALL")
    memory_candidate_ttl_seconds: int = Field(
        default=7 * 24 * 3600, gt=0, alias="MEMORY_CANDIDATE_TTL_SECONDS"
    )
    memory_default_retention_days: int = Field(
        default=180, gt=0, alias="MEMORY_DEFAULT_RETENTION_DAYS"
    )
    memory_extraction_enabled: bool = Field(
        default=True, alias="MEMORY_EXTRACTION_ENABLED"
    )
    memory_chroma_collection_name: str = Field(
        default="middle_platform_memories", alias="MEMORY_CHROMA_COLLECTION_NAME"
    )
    memory_worker_enabled: bool = Field(
        default=True, alias="MEMORY_WORKER_ENABLED"
    )
    memory_worker_poll_seconds: float = Field(
        default=2.0, gt=0, alias="MEMORY_WORKER_POLL_SECONDS"
    )
    memory_worker_stale_seconds: int = Field(
        default=300, ge=0, alias="MEMORY_WORKER_STALE_SECONDS"
    )
    memory_auto_confirm_seconds: int = Field(
        default=24 * 3600, gt=0, alias="MEMORY_AUTO_CONFIRM_SECONDS"
    )
    memory_maintenance_interval_seconds: int = Field(
        default=60, ge=1, le=3600, alias="MEMORY_MAINTENANCE_INTERVAL_SECONDS"
    )
    memory_summary_max_chars: int = Field(
        default=2000, ge=200, le=8000, alias="MEMORY_SUMMARY_MAX_CHARS"
    )
    memory_incident_candidate_ttl_seconds: int = Field(
        default=7 * 24 * 3600,
        gt=0,
        alias="MEMORY_INCIDENT_CANDIDATE_TTL_SECONDS",
    )
    memory_entity_recall_limit: int = Field(
        default=5, ge=1, le=20, alias="MEMORY_ENTITY_RECALL_LIMIT"
    )
    memory_procedural_enabled: bool = Field(
        default=True, alias="MEMORY_PROCEDURAL_ENABLED"
    )
    memory_procedural_guidance_enabled: bool = Field(
        default=False, alias="MEMORY_PROCEDURAL_GUIDANCE_ENABLED"
    )
    memory_procedural_observe_only: bool = Field(
        default=True, alias="MEMORY_PROCEDURAL_OBSERVE_ONLY"
    )
    memory_procedural_recall_limit: int = Field(
        default=3, ge=1, le=10, alias="MEMORY_PROCEDURAL_RECALL_LIMIT"
    )
    memory_domain_promotion_enabled: bool = Field(
        default=False, alias="MEMORY_DOMAIN_PROMOTION_ENABLED"
    )
    memory_domain_default_retention_days: int = Field(
        default=90, ge=1, le=3650, alias="MEMORY_DOMAIN_DEFAULT_RETENTION_DAYS"
    )
    memory_conflict_review_threshold: int = Field(
        default=2, ge=1, le=20, alias="MEMORY_CONFLICT_REVIEW_THRESHOLD"
    )
    agent_quality_enabled: bool = Field(default=True, alias="AGENT_QUALITY_ENABLED")
    agent_quality_db: Path = Field(
        default=Path("storage/agent_quality.db"),
        alias="AGENT_QUALITY_DB",
    )
    agent_quality_running_timeout_seconds: int = Field(
        default=600,
        ge=30,
        alias="AGENT_QUALITY_RUNNING_TIMEOUT_SECONDS",
    )
    agent_quality_page_size: int = Field(
        default=20,
        ge=1,
        le=200,
        alias="AGENT_QUALITY_PAGE_SIZE",
    )
    agent_quality_semantic_judge_enabled: bool = Field(
        default=True, alias="AGENT_QUALITY_SEMANTIC_JUDGE_ENABLED"
    )
    agent_quality_judge_model: str = Field(
        default="deepseek-v4-flash",
        alias="AGENT_QUALITY_JUDGE_MODEL",
    )
    agent_quality_judge_timeout_seconds: float = Field(
        default=20,
        gt=0,
        le=120,
        alias="AGENT_QUALITY_JUDGE_TIMEOUT_SECONDS",
    )
    agent_quality_eval_worker_enabled: bool = Field(
        default=True, alias="AGENT_QUALITY_EVAL_WORKER_ENABLED"
    )
    agent_quality_scheduled_eval_enabled: bool = Field(
        default=True, alias="AGENT_QUALITY_SCHEDULED_EVAL_ENABLED"
    )
    agent_quality_eval_poll_seconds: float = Field(
        default=2.0, gt=0, alias="AGENT_QUALITY_EVAL_POLL_SECONDS"
    )
    agent_quality_eval_stale_seconds: int = Field(
        default=300, ge=0, alias="AGENT_QUALITY_EVAL_STALE_SECONDS"
    )
    agent_quality_eval_case_timeout_seconds: float = Field(
        default=120.0, gt=0, alias="AGENT_QUALITY_EVAL_CASE_TIMEOUT_SECONDS"
    )
    agent_application_version: str = Field(
        default="0.1.0",
        alias="AGENT_APPLICATION_VERSION",
    )
    agent_prompt_version: str = Field(default="v1", alias="AGENT_PROMPT_VERSION")
    agent_tracing_enabled: bool = Field(default=True, alias="AGENT_TRACING_ENABLED")
    agent_trace_include_sensitive_data: bool = Field(
        default=False,
        alias="AGENT_TRACE_INCLUDE_SENSITIVE_DATA",
    )
    agent_tracing_api_key: str = Field(default="", alias="AGENT_TRACING_API_KEY")

    feishu_bot_enabled: bool = Field(default=False, alias="FEISHU_BOT_ENABLED")
    feishu_app_id: str = Field(default="", alias="FEISHU_APP_ID")
    feishu_app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")
    feishu_event_db: Path = Field(
        default=Path("storage/feishu_bot.db"),
        alias="FEISHU_EVENT_DB",
    )
    feishu_reply_max_chars: int = Field(
        default=3500,
        ge=500,
        le=10000,
        alias="FEISHU_REPLY_MAX_CHARS",
    )
    feishu_group_require_mention: bool = Field(
        default=True,
        alias="FEISHU_GROUP_REQUIRE_MENTION",
    )
    feishu_thread_isolation_enabled: bool = Field(
        default=True, alias="FEISHU_THREAD_ISOLATION_ENABLED"
    )
    feishu_agent_timeout_seconds: float = Field(
        default=180.0,
        gt=0,
        alias="FEISHU_AGENT_TIMEOUT_SECONDS",
    )
    user_auth_enabled: bool = Field(default=True, alias="USER_AUTH_ENABLED")
    user_auth_db: Path = Field(
        default=Path("storage/user_auth.db"), alias="USER_AUTH_DB"
    )
    user_public_base_url: str = Field(
        default="http://172.18.26.1:8000",
        pattern=r"^https?://[^/]+(?:/)?$",
        alias="USER_PUBLIC_BASE_URL",
    )
    feishu_oauth_enabled: bool = Field(
        default=True, alias="FEISHU_OAUTH_ENABLED"
    )
    feishu_oauth_app_id: str = Field(default="", alias="FEISHU_OAUTH_APP_ID")
    feishu_oauth_app_secret: str = Field(
        default="", alias="FEISHU_OAUTH_APP_SECRET"
    )
    feishu_tenant_key: str = Field(default="", alias="FEISHU_TENANT_KEY")
    anonymous_cookie_name: str = Field(
        default="knowledge_anon", alias="ANONYMOUS_COOKIE_NAME"
    )
    anonymous_device_ttl_seconds: int = Field(
        default=180 * 24 * 3600, gt=0, alias="ANONYMOUS_DEVICE_TTL_SECONDS"
    )
    user_session_cookie_name: str = Field(
        default="knowledge_user", alias="USER_SESSION_COOKIE_NAME"
    )
    user_session_sliding_ttl_seconds: int = Field(
        default=7 * 24 * 3600,
        gt=0,
        alias="USER_SESSION_SLIDING_TTL_SECONDS",
    )
    user_session_absolute_ttl_seconds: int = Field(
        default=30 * 24 * 3600,
        gt=0,
        alias="USER_SESSION_ABSOLUTE_TTL_SECONDS",
    )
    oauth_state_ttl_seconds: int = Field(
        default=600, gt=0, alias="OAUTH_STATE_TTL_SECONDS"
    )
    user_cookie_secure: bool = Field(default=False, alias="USER_COOKIE_SECURE")

    metric_mcp_enabled: bool = Field(default=True, alias="METRIC_MCP_ENABLED")
    metric_query_guard_enabled: bool = Field(
        default=True,
        alias="METRIC_QUERY_GUARD_ENABLED",
    )
    metric_mcp_url: str = Field(default="", alias="METRIC_MCP_URL")
    metric_mcp_bearer_token: str = Field(default="", alias="METRIC_MCP_BEARER_TOKEN")
    metric_mcp_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        alias="METRIC_MCP_TIMEOUT_SECONDS",
    )

    grafana_log_enabled: bool = Field(default=False, alias="GRAFANA_LOG_ENABLED")
    grafana_log_url: str = Field(default="", alias="GRAFANA_LOG_URL")
    grafana_log_bearer_token: str = Field(
        default="",
        alias="GRAFANA_LOG_BEARER_TOKEN",
    )
    grafana_log_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        alias="GRAFANA_LOG_TIMEOUT_SECONDS",
    )
    grafana_log_max_entries: int = Field(
        default=20,
        gt=0,
        alias="GRAFANA_LOG_MAX_ENTRIES",
    )
    grafana_log_max_entry_chars: int = Field(
        default=2000,
        gt=0,
        alias="GRAFANA_LOG_MAX_ENTRY_CHARS",
    )
    grafana_log_max_total_chars: int = Field(
        default=30000,
        gt=0,
        alias="GRAFANA_LOG_MAX_TOTAL_CHARS",
    )
    grafana_log_max_range_minutes: int = Field(
        default=1440,
        gt=0,
        alias="GRAFANA_LOG_MAX_RANGE_MINUTES",
    )
    grafana_log_app_label: str = Field(
        default="api-center-server",
        pattern=r"^[A-Za-z0-9_.-]+$",
        alias="GRAFANA_LOG_APP_LABEL",
    )
    grafana_log_query_max_lines: int = Field(
        default=1000,
        ge=1,
        le=5000,
        alias="GRAFANA_LOG_QUERY_MAX_LINES",
    )
    grafana_develop_datasource_uid: str = Field(
        default="",
        alias="GRAFANA_DEVELOP_DATASOURCE_UID",
    )
    grafana_develop_namespace: str = Field(
        default="",
        alias="GRAFANA_DEVELOP_NAMESPACE",
    )
    grafana_develop_code_branch: str = Field(
        default="develop",
        alias="GRAFANA_DEVELOP_CODE_BRANCH",
    )
    grafana_test_datasource_uid: str = Field(
        default="",
        alias="GRAFANA_TEST_DATASOURCE_UID",
    )
    grafana_test_namespace: str = Field(default="", alias="GRAFANA_TEST_NAMESPACE")
    grafana_test_code_branch: str = Field(
        default="develop",
        alias="GRAFANA_TEST_CODE_BRANCH",
    )
    grafana_prod_datasource_uid: str = Field(
        default="",
        alias="GRAFANA_PROD_DATASOURCE_UID",
    )
    grafana_prod_namespace: str = Field(default="", alias="GRAFANA_PROD_NAMESPACE")
    grafana_prod_code_branch: str = Field(
        default="master",
        alias="GRAFANA_PROD_CODE_BRANCH",
    )

    bug_graph_enabled: bool = Field(default=True, alias="BUG_GRAPH_ENABLED")
    bug_graph_checkpoint_provider: str = Field(
        default="auto",
        alias="BUG_GRAPH_CHECKPOINT_PROVIDER",
    )
    bug_graph_db: Path = Field(
        default=Path("storage/bug_graph.db"),
        alias="BUG_GRAPH_DB",
    )
    bug_graph_interrupt_ttl_seconds: int = Field(
        default=86400,
        gt=0,
        alias="BUG_GRAPH_INTERRUPT_TTL_SECONDS",
    )
    bug_graph_log_retry_count: int = Field(
        default=2,
        ge=0,
        alias="BUG_GRAPH_LOG_RETRY_COUNT",
    )
    bug_graph_log_range_minutes: int = Field(
        default=1440,
        gt=0,
        alias="BUG_GRAPH_LOG_RANGE_MINUTES",
    )
    bug_graph_code_top_k: int = Field(
        default=5,
        gt=0,
        alias="BUG_GRAPH_CODE_TOP_K",
    )
    bug_graph_min_rerank_score: float = Field(
        default=0.35,
        ge=0,
        le=1,
        alias="BUG_GRAPH_MIN_RERANK_SCORE",
    )
    citation_detail_max_chars: int = Field(
        default=6000,
        gt=0,
        alias="CITATION_DETAIL_MAX_CHARS",
    )

    knowledge_catalog_db: Path = Field(
        default=Path("storage/knowledge_catalog.db"),
        alias="KNOWLEDGE_CATALOG_DB",
    )
    knowledge_storage_root: Path = Field(
        default=Path("storage"),
        alias="KNOWLEDGE_STORAGE_ROOT",
    )
    frontend_dist: Path = Field(default=Path("web/dist"), alias="FRONTEND_DIST")
    gitlab_base_url: str = Field(default="", alias="GITLAB_BASE_URL")
    gitlab_access_token: str = Field(default="", alias="GITLAB_ACCESS_TOKEN")
    git_sync_interval_seconds: int = Field(
        default=600,
        gt=0,
        alias="GIT_SYNC_INTERVAL_SECONDS",
    )
    git_command_timeout_seconds: float = Field(
        default=1800.0,
        gt=0,
        alias="GIT_COMMAND_TIMEOUT_SECONDS",
    )
    source_worker_enabled: bool = Field(default=True, alias="SOURCE_WORKER_ENABLED")
    source_worker_poll_seconds: float = Field(
        default=2.0,
        gt=0,
        alias="SOURCE_WORKER_POLL_SECONDS",
    )
    source_worker_stale_seconds: int = Field(
        default=900,
        gt=0,
        alias="SOURCE_WORKER_STALE_SECONDS",
    )
    knowledge_secret_master_key: str = Field(
        default="",
        alias="KNOWLEDGE_SECRET_MASTER_KEY",
    )
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password_hash: str = Field(default="", alias="ADMIN_PASSWORD_HASH")
    admin_session_ttl_seconds: int = Field(
        default=8 * 60 * 60,
        gt=0,
        alias="ADMIN_SESSION_TTL_SECONDS",
    )
    admin_cookie_secure: bool = Field(default=False, alias="ADMIN_COOKIE_SECURE")
    admin_cookie_name: str = Field(default="knowledge_admin", alias="ADMIN_COOKIE_NAME")
    swagger_allowed_hosts: str = Field(default="", alias="SWAGGER_ALLOWED_HOSTS")
    upload_max_file_bytes: int = Field(
        default=50 * 1024 * 1024,
        gt=0,
        alias="UPLOAD_MAX_FILE_BYTES",
    )
    upload_max_batch_bytes: int = Field(
        default=500 * 1024 * 1024,
        gt=0,
        alias="UPLOAD_MAX_BATCH_BYTES",
    )
    upload_max_files: int = Field(default=2000, gt=0, alias="UPLOAD_MAX_FILES")

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = str(value).strip().upper()
        if normalized not in VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(VALID_LOG_LEVELS))
            raise ValueError(f"LOG_LEVEL must be one of: {allowed}")
        return normalized

    @field_validator("agent_model_provider", mode="before")
    @classmethod
    def normalize_agent_provider(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in VALID_AGENT_PROVIDERS:
            allowed = ", ".join(sorted(VALID_AGENT_PROVIDERS))
            raise ValueError(f"AGENT_MODEL_PROVIDER must be one of: {allowed}")
        return normalized

    @field_validator("data_store_provider", mode="before")
    @classmethod
    def normalize_data_store_provider(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in VALID_DATA_STORE_PROVIDERS:
            allowed = ", ".join(sorted(VALID_DATA_STORE_PROVIDERS))
            raise ValueError(f"DATA_STORE_PROVIDER must be one of: {allowed}")
        return normalized

    @field_validator("database_ssl_mode", mode="before")
    @classmethod
    def normalize_database_ssl_mode(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in VALID_DATABASE_SSL_MODES:
            allowed = ", ".join(sorted(VALID_DATABASE_SSL_MODES))
            raise ValueError(f"DATABASE_SSL_MODE must be one of: {allowed}")
        return normalized

    @field_validator("bug_graph_checkpoint_provider", mode="before")
    @classmethod
    def normalize_bug_graph_checkpoint_provider(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in VALID_BUG_GRAPH_CHECKPOINT_PROVIDERS:
            allowed = ", ".join(sorted(VALID_BUG_GRAPH_CHECKPOINT_PROVIDERS))
            raise ValueError(
                f"BUG_GRAPH_CHECKPOINT_PROVIDER must be one of: {allowed}"
            )
        return normalized

    @field_validator("vector_store_provider", mode="before")
    @classmethod
    def normalize_vector_store_provider(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in VALID_VECTOR_STORE_PROVIDERS:
            allowed = ", ".join(sorted(VALID_VECTOR_STORE_PROVIDERS))
            raise ValueError(f"VECTOR_STORE_PROVIDER must be one of: {allowed}")
        return normalized

    @field_validator("database_schema", "pgvector_schema", "pgvector_table")
    @classmethod
    def validate_postgres_identifier(cls, value: str) -> str:
        normalized = str(value).strip()
        if not POSTGRES_IDENTIFIER_PATTERN.fullmatch(normalized):
            raise ValueError("PostgreSQL identifiers must contain only letters, digits, and underscores")
        return normalized

    @field_validator("user_public_base_url", mode="before")
    @classmethod
    def normalize_user_public_base_url(cls, value: str) -> str:
        return str(value).strip().rstrip("/")

    @model_validator(mode="after")
    def validate_feishu_credentials(self):
        if self.feishu_bot_enabled and not (
            _usable(self.feishu_app_id) and _usable(self.feishu_app_secret)
        ):
            raise ValueError(
                "FEISHU_APP_ID and FEISHU_APP_SECRET are required when "
                "FEISHU_BOT_ENABLED=true"
            )
        oauth_id = bool(_usable(self.feishu_oauth_app_id))
        oauth_secret = bool(_usable(self.feishu_oauth_app_secret))
        if oauth_id != oauth_secret:
            raise ValueError(
                "FEISHU_OAUTH_APP_ID and FEISHU_OAUTH_APP_SECRET "
                "must be configured together"
            )
        if (
            self.feishu_oauth_enabled
            and not (oauth_id and oauth_secret)
            and bool(_usable(self.feishu_app_id))
            != bool(_usable(self.feishu_app_secret))
        ):
            raise ValueError(
                "Feishu OAuth fallback requires both FEISHU_APP_ID "
                "and FEISHU_APP_SECRET"
            )
        if (
            self.user_session_sliding_ttl_seconds
            > self.user_session_absolute_ttl_seconds
        ):
            raise ValueError(
                "user session sliding TTL cannot exceed absolute TTL"
            )
        requires_postgres = bool(
            self.data_store_provider == "postgres"
            or self.vector_store_provider == "pgvector"
            or self.vector_shadow_enabled
        )
        if requires_postgres:
            if not _usable(self.database_url) and not all(
                _usable(value)
                for value in (
                    self.pghost,
                    self.pgdatabase,
                    self.pguser,
                    self.pgpassword,
                )
            ):
                raise ValueError(
                    "PostgreSQL configuration requires DATABASE_URL or complete "
                    "PGHOST/PGDATABASE/PGUSER/PGPASSWORD values"
                )
            self._normalize_database_url(self.resolved_database_url)
        elif _usable(self.database_url):
            self._normalize_database_url(self.database_url)
        if self.vector_store_provider == "pgvector" and (
            self.pgvector_dimensions != 1024
            or self.embedding_dimensions != self.pgvector_dimensions
        ):
            raise ValueError("pgvector requires a fixed 1024-dimensional embedding")
        return self

    @property
    def resolved_database_url(self) -> str:
        configured = _usable(self.database_url)
        if configured:
            return self._normalize_database_url(configured)
        username = quote(_usable(self.pguser), safe="")
        password = quote(_usable(self.pgpassword), safe="")
        host = self.pghost.strip()
        database = quote(self.pgdatabase.strip(), safe="")
        return (
            f"postgresql+asyncpg://{username}:{password}@{host}:"
            f"{self.pgport}/{database}"
        )

    @property
    def resolved_psycopg_url(self) -> str:
        url = self.resolved_database_url.replace(
            "postgresql+asyncpg://",
            "postgresql://",
            1,
        )
        if self.database_ssl_mode == "prefer":
            return url
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["sslmode"] = self.database_ssl_mode
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
        )

    @staticmethod
    def _normalize_database_url(value: str) -> str:
        normalized = value.strip()
        if normalized.startswith("postgresql+asyncpg://"):
            return normalized
        if normalized.startswith("postgresql://"):
            return normalized.replace(
                "postgresql://",
                "postgresql+asyncpg://",
                1,
            )
        if normalized.startswith("postgres://"):
            return normalized.replace(
                "postgres://",
                "postgresql+asyncpg://",
                1,
            )
        raise ValueError("DATABASE_URL must use a PostgreSQL URL scheme")

    @property
    def resolved_embedding_api_key(self) -> str:
        return (
            _usable(self.embedding_api_key)
            or _usable(self.dashscope_api_key)
            or _usable(self.legacy_openai_api_key)
        )

    @property
    def resolved_embedding_base_url(self) -> str:
        return (
            self.embedding_base_url
            or self.legacy_openai_base_url
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

    @property
    def resolved_deepseek_api_key(self) -> str:
        return _usable(self.deepseek_api_key)

    @property
    def resolved_rerank_api_key(self) -> str:
        return (
            _usable(self.rerank_api_key)
            or _usable(self.dashscope_api_key)
            or _usable(self.embedding_api_key)
            or _usable(self.legacy_openai_api_key)
        )

    @property
    def resolved_log_file(self) -> Path:
        path = self.log_file.expanduser()
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    @property
    def resolved_agent_session_db(self) -> Path:
        path = self.agent_session_db.expanduser()
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    @property
    def resolved_memory_db(self) -> Path:
        return self._resolve_project_path(self.memory_db)

    @property
    def resolved_agent_quality_db(self) -> Path:
        return self._resolve_project_path(self.agent_quality_db)

    @property
    def resolved_agent_openai_api_key(self) -> str:
        return _usable(self.agent_openai_api_key)

    @property
    def resolved_agent_tracing_api_key(self) -> str:
        return _usable(self.agent_tracing_api_key)

    @property
    def resolved_feishu_app_id(self) -> str:
        return _usable(self.feishu_app_id)

    @property
    def resolved_feishu_app_secret(self) -> str:
        return _usable(self.feishu_app_secret)

    @property
    def resolved_feishu_oauth_app_id(self) -> str:
        return _usable(self.feishu_oauth_app_id) or self.resolved_feishu_app_id

    @property
    def resolved_feishu_oauth_app_secret(self) -> str:
        return (
            _usable(self.feishu_oauth_app_secret)
            or self.resolved_feishu_app_secret
        )

    @property
    def resolved_feishu_event_db(self) -> Path:
        return self._resolve_project_path(self.feishu_event_db)

    @property
    def resolved_user_auth_db(self) -> Path:
        return self._resolve_project_path(self.user_auth_db)

    @property
    def feishu_oauth_callback_url(self) -> str:
        return f"{self.user_public_base_url}/api/v1/auth/feishu/callback"

    @property
    def feishu_oauth_available(self) -> bool:
        return bool(
            self.user_auth_enabled
            and self.feishu_oauth_enabled
            and self.resolved_feishu_oauth_app_id
            and self.resolved_feishu_oauth_app_secret
        )

    @property
    def resolved_metric_mcp_bearer_token(self) -> str:
        return _usable(self.metric_mcp_bearer_token)

    @property
    def resolved_grafana_log_bearer_token(self) -> str:
        return _usable(self.grafana_log_bearer_token)

    @property
    def resolved_bug_graph_db(self) -> Path:
        return self._resolve_project_path(self.bug_graph_db)

    @property
    def grafana_log_targets(self) -> dict[str, tuple[str, str, str]]:
        return {
            "develop": (
                self.grafana_develop_datasource_uid.strip(),
                self.grafana_develop_namespace.strip(),
                self.grafana_develop_code_branch.strip(),
            ),
            "test": (
                self.grafana_test_datasource_uid.strip(),
                self.grafana_test_namespace.strip(),
                self.grafana_test_code_branch.strip(),
            ),
            "prod": (
                self.grafana_prod_datasource_uid.strip(),
                self.grafana_prod_namespace.strip(),
                self.grafana_prod_code_branch.strip(),
            ),
        }

    @staticmethod
    def _resolve_project_path(path: Path) -> Path:
        expanded = path.expanduser()
        return expanded if expanded.is_absolute() else (PROJECT_ROOT / expanded).resolve()

    @property
    def resolved_knowledge_catalog_db(self) -> Path:
        return self._resolve_project_path(self.knowledge_catalog_db)

    @property
    def resolved_knowledge_storage_root(self) -> Path:
        return self._resolve_project_path(self.knowledge_storage_root)

    @property
    def resolved_frontend_dist(self) -> Path:
        return self._resolve_project_path(self.frontend_dist)

    @property
    def resolved_gitlab_access_token(self) -> str:
        return _usable(self.gitlab_access_token)

    @property
    def resolved_knowledge_secret_master_key(self) -> str:
        return _usable(self.knowledge_secret_master_key)

    @property
    def swagger_allowed_host_set(self) -> set[str]:
        return {
            host.strip().lower()
            for host in self.swagger_allowed_hosts.split(",")
            if host.strip()
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
