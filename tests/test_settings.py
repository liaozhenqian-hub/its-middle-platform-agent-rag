from pathlib import Path

import pytest
from pydantic import ValidationError

from knowledge.config.settings import PROJECT_ROOT, Settings


def test_settings_default_env_file_is_anchored_to_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)

    env_file = Path(Settings.model_config["env_file"])

    assert env_file.is_absolute()
    assert env_file == PROJECT_ROOT / ".env"


def test_settings_support_deepseek_and_embedding_fields():
    settings = Settings(
        _env_file=None,
        EMBEDDING_API_KEY="embedding-key",
        EMBEDDING_BASE_URL="https://embedding.example.com/v1",
        EMBEDDING_MODEL="text-embedding-3-small",
        EMBEDDING_DIMENSIONS=1024,
        EMBEDDING_BATCH_SIZE=10,
        DEEPSEEK_API_KEY="deepseek-key",
        DEEPSEEK_BASE_URL="https://api.deepseek.com",
        DEEPSEEK_CHAT_MODEL="deepseek-v4-flash",
    )

    assert settings.resolved_embedding_api_key == "embedding-key"
    assert settings.resolved_embedding_base_url == "https://embedding.example.com/v1"
    assert settings.embedding_dimensions == 1024
    assert settings.embedding_batch_size == 10
    assert settings.deepseek_api_key == "deepseek-key"
    assert settings.deepseek_chat_model == "deepseek-v4-flash"
    assert settings.deepseek_reasoning_model == "deepseek-v4-pro"
    assert settings.deepseek_reasoning_enabled is True


def test_settings_exposes_manager_reasoning_defaults():
    settings = Settings(_env_file=None)

    assert settings.agent_manager_reasoning_enabled is True
    assert settings.agent_manager_reasoning_timeout_seconds == 60.0


@pytest.mark.parametrize("timeout", [0, -1, 181])
def test_settings_rejects_invalid_manager_reasoning_timeout(timeout: float):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            AGENT_MANAGER_REASONING_TIMEOUT_SECONDS=timeout,
        )


def test_legacy_openai_embedding_fields_still_work():
    settings = Settings(
        _env_file=None,
        OPENAI_API_KEY="legacy-key",
        OPENAI_BASE_URL="https://legacy.example.com/v1",
    )

    assert settings.resolved_embedding_api_key == "legacy-key"
    assert settings.resolved_embedding_base_url == "https://legacy.example.com/v1"


def test_dashscope_api_key_can_be_used_for_bailian_embedding():
    settings = Settings(
        _env_file=None,
        DASHSCOPE_API_KEY="dashscope-key",
    )

    assert settings.resolved_embedding_api_key == "dashscope-key"


def test_settings_default_to_aliyun_bailian_embedding():
    settings = Settings(_env_file=None)

    assert settings.embedding_model == "text-embedding-v4"
    assert settings.resolved_embedding_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert settings.embedding_dimensions == 1024
    assert settings.embedding_batch_size == 10


def test_settings_exposes_default_bm25_field_weights():
    settings = Settings(_env_file=None)

    assert settings.bm25_title_weight == 0.65
    assert settings.bm25_keywords_weight == 0.35


def test_settings_exposes_query_rewrite_and_rerank_defaults():
    settings = Settings(
        _env_file=None,
        DEEPSEEK_API_KEY="deepseek-key",
        DASHSCOPE_API_KEY="dashscope-key",
    )

    assert settings.query_rewrite_enabled is True
    assert settings.resolved_deepseek_api_key == "deepseek-key"
    assert settings.rerank_enabled is True
    assert settings.rerank_model == "qwen3-rerank"
    assert settings.resolved_rerank_api_key == "dashscope-key"
    assert settings.rerank_base_url == "https://dashscope.aliyuncs.com/compatible-api/v1"
    assert settings.keyword_candidate_k == 20
    assert settings.vector_candidate_k == 20
    assert settings.final_result_k == 5
    assert settings.retrieval_warmup_enabled is True
    assert settings.bm25_memory_filter_enabled is True
    assert settings.bm25_stale_while_refresh_enabled is True
    assert settings.retrieval_parallel_routes_enabled is True


def test_retrieval_performance_switches_can_be_disabled():
    settings = Settings(
        _env_file=None,
        BM25_MEMORY_FILTER_ENABLED=False,
        BM25_STALE_WHILE_REFRESH_ENABLED=False,
        RETRIEVAL_PARALLEL_ROUTES_ENABLED=False,
    )

    assert settings.bm25_memory_filter_enabled is False
    assert settings.bm25_stale_while_refresh_enabled is False
    assert settings.retrieval_parallel_routes_enabled is False


def test_settings_exposes_logging_defaults():
    settings = Settings(_env_file=None)

    assert settings.log_level == "INFO"
    assert settings.log_file == Path("logs/knowledge-rag.log")
    assert settings.resolved_log_file == PROJECT_ROOT / "logs/knowledge-rag.log"
    assert settings.log_max_bytes == 10 * 1024 * 1024
    assert settings.log_backup_count == 5


def test_settings_exposes_agent_runtime_defaults():
    settings = Settings(_env_file=None)

    assert settings.agent_model_provider == "openai"
    assert settings.agent_model_name == "gpt-5.4-mini"
    assert settings.agent_openai_api_key == ""
    assert settings.agent_openai_base_url == "https://api.openai.com/v1"
    assert settings.agent_max_turns == 12
    assert settings.agent_session_history_limit == 50
    assert settings.agent_tracing_enabled is True
    assert settings.agent_trace_include_sensitive_data is False
    assert settings.metric_mcp_enabled is True
    assert settings.metric_mcp_url == ""
    assert settings.metric_mcp_bearer_token == ""
    assert settings.metric_mcp_timeout_seconds == 30.0
    assert settings.resolved_agent_session_db == PROJECT_ROOT / "storage/agent_sessions.db"


def test_settings_exposes_agent_quality_defaults_and_resolves_path():
    settings = Settings(_env_file=None)

    assert settings.agent_quality_enabled is True
    assert settings.resolved_agent_quality_db == PROJECT_ROOT / "storage/agent_quality.db"
    assert settings.agent_quality_running_timeout_seconds == 600
    assert settings.agent_quality_page_size == 20
    assert settings.agent_application_version == "0.1.0"
    assert settings.agent_prompt_version == "v1"

    custom = Settings(_env_file=None, AGENT_QUALITY_DB="custom/quality.db")
    assert custom.resolved_agent_quality_db == PROJECT_ROOT / "custom/quality.db"


def test_settings_exposes_bounded_grafana_log_defaults():
    settings = Settings(_env_file=None)

    assert settings.grafana_log_enabled is False
    assert settings.grafana_log_url == ""
    assert settings.resolved_grafana_log_bearer_token == ""
    assert settings.grafana_log_timeout_seconds == 15.0
    assert settings.grafana_log_max_entries == 20
    assert settings.grafana_log_max_entry_chars == 2000
    assert settings.grafana_log_max_total_chars == 30000
    assert settings.grafana_log_app_label == "api-center-server"
    assert settings.grafana_log_query_max_lines == 1000
    assert settings.grafana_develop_code_branch == "develop"
    assert settings.grafana_test_code_branch == "develop"
    assert settings.grafana_prod_code_branch == "master"


def test_settings_exposes_bug_graph_and_citation_detail_defaults():
    settings = Settings(_env_file=None)

    assert settings.bug_graph_enabled is True
    assert settings.bug_graph_checkpoint_provider == "auto"
    assert settings.resolved_bug_graph_db == PROJECT_ROOT / "storage/bug_graph.db"
    assert settings.bug_graph_interrupt_ttl_seconds == 86400
    assert settings.bug_graph_log_retry_count == 2
    assert settings.bug_graph_log_range_minutes == 1440
    assert settings.bug_graph_code_top_k == 5
    assert settings.bug_graph_min_rerank_score == 0.35
    assert settings.citation_detail_max_chars == 6000


def test_settings_exposes_agent_reliability_defaults():
    settings = Settings(_env_file=None)

    assert settings.agent_intent_router_enabled is True
    assert settings.agent_llm_router_enabled is True
    assert settings.agent_intent_router_min_confidence == 0.75
    assert settings.agent_retrieval_max_calls == 4
    assert settings.agent_retrieval_max_identical_queries == 1
    assert settings.agent_public_citation_limit == 5
    assert settings.agent_citation_min_rerank_score == 0.35
    assert settings.agent_citation_min_rrf_score == 0.02
    assert settings.metric_query_guard_enabled is True
    assert settings.grafana_log_max_range_minutes == 1440


def test_settings_resolves_grafana_targets_and_secret_placeholder():
    settings = Settings(
        _env_file=None,
        GRAFANA_LOG_BEARER_TOKEN="<fill-locally>",
        GRAFANA_DEVELOP_DATASOURCE_UID="dev-uid",
        GRAFANA_DEVELOP_NAMESPACE="middle-develop",
        GRAFANA_TEST_DATASOURCE_UID="test-uid",
        GRAFANA_TEST_NAMESPACE="middle-test",
        GRAFANA_PROD_DATASOURCE_UID="prod-uid",
        GRAFANA_PROD_NAMESPACE="middle-prod",
    )

    assert settings.resolved_grafana_log_bearer_token == ""
    assert settings.grafana_log_targets == {
        "develop": ("dev-uid", "middle-develop", "develop"),
        "test": ("test-uid", "middle-test", "develop"),
        "prod": ("prod-uid", "middle-prod", "master"),
    }


def test_settings_normalizes_agent_provider_and_resolves_absolute_session_path(
    tmp_path: Path,
):
    database = tmp_path / "sessions.db"
    settings = Settings(
        _env_file=None,
        AGENT_MODEL_PROVIDER=" DeepSeek ",
        AGENT_SESSION_DB=database,
    )

    assert settings.agent_model_provider == "deepseek"
    assert settings.resolved_agent_session_db == database


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("AGENT_MODEL_PROVIDER", "other"),
        ("AGENT_MAX_TURNS", 0),
        ("AGENT_SESSION_HISTORY_LIMIT", 0),
        ("METRIC_MCP_TIMEOUT_SECONDS", 0),
        ("GRAFANA_LOG_TIMEOUT_SECONDS", 0),
        ("GRAFANA_LOG_MAX_ENTRIES", 0),
        ("GRAFANA_LOG_MAX_ENTRY_CHARS", 0),
        ("GRAFANA_LOG_MAX_TOTAL_CHARS", 0),
        ("GRAFANA_LOG_MAX_RANGE_MINUTES", 0),
        ("GRAFANA_LOG_QUERY_MAX_LINES", 0),
        ("GRAFANA_LOG_APP_LABEL", 'api-center-server"} |= "secret'),
        ("BUG_GRAPH_INTERRUPT_TTL_SECONDS", 0),
        ("BUG_GRAPH_LOG_RETRY_COUNT", -1),
        ("BUG_GRAPH_LOG_RANGE_MINUTES", 0),
        ("BUG_GRAPH_CODE_TOP_K", 0),
        ("BUG_GRAPH_MIN_RERANK_SCORE", -0.1),
        ("BUG_GRAPH_MIN_RERANK_SCORE", 1.1),
        ("CITATION_DETAIL_MAX_CHARS", 0),
    ],
)
def test_settings_rejects_invalid_agent_configuration(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_settings_exposes_multi_source_defaults():
    settings = Settings(_env_file=None)

    assert settings.resolved_knowledge_catalog_db == PROJECT_ROOT / "storage/knowledge_catalog.db"
    assert settings.resolved_knowledge_storage_root == PROJECT_ROOT / "storage"
    assert settings.git_sync_interval_seconds == 600
    assert settings.git_command_timeout_seconds == 1800
    assert settings.source_worker_enabled is True
    assert settings.source_worker_poll_seconds == 2.0
    assert settings.source_worker_stale_seconds == 900
    assert settings.admin_username == "admin"
    assert settings.admin_session_ttl_seconds == 8 * 60 * 60
    assert settings.admin_cookie_secure is False
    assert settings.swagger_allowed_host_set == set()
    assert settings.upload_max_file_bytes == 50 * 1024 * 1024
    assert settings.upload_max_batch_bytes == 500 * 1024 * 1024
    assert settings.upload_max_files == 2000
    assert settings.resolved_frontend_dist == PROJECT_ROOT / "web/dist"


def test_settings_normalizes_swagger_hosts_and_secret_placeholders(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        KNOWLEDGE_CATALOG_DB=tmp_path / "catalog.db",
        KNOWLEDGE_STORAGE_ROOT=tmp_path / "sources",
        FRONTEND_DIST=tmp_path / "dist",
        SWAGGER_ALLOWED_HOSTS=" api.internal,swagger.internal, API.INTERNAL ",
        GITLAB_ACCESS_TOKEN="<fill-locally>",
        KNOWLEDGE_SECRET_MASTER_KEY="<fill-locally>",
    )

    assert settings.resolved_knowledge_catalog_db == tmp_path / "catalog.db"
    assert settings.resolved_knowledge_storage_root == tmp_path / "sources"
    assert settings.resolved_frontend_dist == tmp_path / "dist"
    assert settings.swagger_allowed_host_set == {"api.internal", "swagger.internal"}
    assert settings.resolved_gitlab_access_token == ""
    assert settings.resolved_knowledge_secret_master_key == ""


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("GIT_SYNC_INTERVAL_SECONDS", 0),
        ("GIT_COMMAND_TIMEOUT_SECONDS", 0),
        ("SOURCE_WORKER_POLL_SECONDS", 0),
        ("SOURCE_WORKER_STALE_SECONDS", 0),
        ("ADMIN_SESSION_TTL_SECONDS", 0),
        ("UPLOAD_MAX_FILE_BYTES", 0),
        ("UPLOAD_MAX_BATCH_BYTES", 0),
        ("UPLOAD_MAX_FILES", 0),
    ],
)
def test_settings_rejects_invalid_multi_source_limits(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_settings_normalizes_lowercase_log_level_and_keeps_absolute_path(tmp_path: Path):
    log_file = tmp_path / "custom.log"
    settings = Settings(
        _env_file=None,
        LOG_LEVEL="debug",
        LOG_FILE=log_file,
    )

    assert settings.log_level == "DEBUG"
    assert settings.resolved_log_file == log_file


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("LOG_LEVEL", "TRACE"),
        ("LOG_MAX_BYTES", 0),
        ("LOG_BACKUP_COUNT", -1),
    ],
)
def test_settings_rejects_invalid_logging_configuration(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_settings_exposes_safe_feishu_defaults():
    settings = Settings(_env_file=None)

    assert settings.feishu_bot_enabled is False
    assert settings.resolved_feishu_app_id == ""
    assert settings.resolved_feishu_app_secret == ""
    assert settings.resolved_feishu_event_db == PROJECT_ROOT / "storage/feishu_bot.db"
    assert settings.feishu_reply_max_chars == 3500
    assert settings.feishu_group_require_mention is True
    assert settings.feishu_agent_timeout_seconds == 180


@pytest.mark.parametrize(
    "configuration",
    [
        {"FEISHU_BOT_ENABLED": True},
        {"FEISHU_BOT_ENABLED": True, "FEISHU_APP_ID": "cli_test"},
        {"FEISHU_BOT_ENABLED": True, "FEISHU_APP_SECRET": "secret"},
    ],
)
def test_settings_requires_complete_feishu_credentials_when_enabled(configuration):
    with pytest.raises(ValidationError, match="FEISHU_APP_ID and FEISHU_APP_SECRET"):
        Settings(_env_file=None, **configuration)


def test_settings_accepts_complete_feishu_configuration(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        FEISHU_BOT_ENABLED=True,
        FEISHU_APP_ID="cli_test",
        FEISHU_APP_SECRET="rotated-secret",
        FEISHU_EVENT_DB=tmp_path / "events.db",
        FEISHU_REPLY_MAX_CHARS=4096,
        FEISHU_AGENT_TIMEOUT_SECONDS=60,
    )

    assert settings.resolved_feishu_event_db == tmp_path / "events.db"
    assert settings.resolved_feishu_app_id == "cli_test"
    assert settings.resolved_feishu_app_secret == "rotated-secret"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("FEISHU_REPLY_MAX_CHARS", 499),
        ("FEISHU_REPLY_MAX_CHARS", 10001),
        ("FEISHU_AGENT_TIMEOUT_SECONDS", 0),
    ],
)
def test_settings_rejects_invalid_feishu_limits(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_settings_exposes_optional_user_identity_defaults():
    settings = Settings(_env_file=None)

    assert settings.user_auth_enabled is True
    assert settings.resolved_user_auth_db == PROJECT_ROOT / "storage/user_auth.db"
    assert settings.user_public_base_url == "http://172.18.26.1:8000"
    assert settings.feishu_oauth_callback_url == (
        "http://172.18.26.1:8000/api/v1/auth/feishu/callback"
    )
    assert settings.feishu_oauth_enabled is True
    assert settings.feishu_oauth_available is False
    assert settings.feishu_tenant_key == ""
    assert settings.anonymous_cookie_name == "knowledge_anon"
    assert settings.anonymous_device_ttl_seconds == 180 * 24 * 3600
    assert settings.user_session_cookie_name == "knowledge_user"
    assert settings.user_session_sliding_ttl_seconds == 7 * 24 * 3600
    assert settings.user_session_absolute_ttl_seconds == 30 * 24 * 3600
    assert settings.oauth_state_ttl_seconds == 600
    assert settings.user_cookie_secure is False


def test_settings_normalizes_user_public_base_url_and_resolves_auth_db(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        USER_AUTH_DB=tmp_path / "auth.db",
        USER_PUBLIC_BASE_URL="http://172.18.26.1:8000/",
    )

    assert settings.resolved_user_auth_db == tmp_path / "auth.db"
    assert settings.user_public_base_url == "http://172.18.26.1:8000"
    assert settings.feishu_oauth_callback_url.endswith(
        "/api/v1/auth/feishu/callback"
    )


def test_settings_enables_feishu_oauth_when_complete_app_credentials_exist():
    unconfigured = Settings(_env_file=None, FEISHU_OAUTH_ENABLED=True)
    assert unconfigured.feishu_oauth_available is False

    with pytest.raises(ValidationError, match="Feishu OAuth"):
        Settings(
            _env_file=None,
            FEISHU_OAUTH_ENABLED=True,
            FEISHU_APP_ID="cli_test",
        )

    configured = Settings(
        _env_file=None,
        FEISHU_OAUTH_ENABLED=True,
        FEISHU_APP_ID="cli_test",
        FEISHU_APP_SECRET="secret",
    )
    assert configured.feishu_oauth_enabled is True
    assert configured.feishu_oauth_available is True


def test_settings_prefers_dedicated_feishu_oauth_credentials():
    settings = Settings(
        _env_file=None,
        FEISHU_APP_ID="cli_bot",
        FEISHU_APP_SECRET="bot-secret",
        FEISHU_OAUTH_APP_ID="cli_oauth",
        FEISHU_OAUTH_APP_SECRET="oauth-secret",
    )

    assert settings.resolved_feishu_app_id == "cli_bot"
    assert settings.resolved_feishu_oauth_app_id == "cli_oauth"
    assert settings.resolved_feishu_oauth_app_secret == "oauth-secret"


def test_settings_falls_back_to_shared_feishu_credentials_for_oauth():
    settings = Settings(
        _env_file=None,
        FEISHU_APP_ID="cli_shared",
        FEISHU_APP_SECRET="shared-secret",
    )

    assert settings.resolved_feishu_oauth_app_id == "cli_shared"
    assert settings.resolved_feishu_oauth_app_secret == "shared-secret"


@pytest.mark.parametrize(
    "values",
    [
        {"FEISHU_OAUTH_APP_ID": "cli_oauth"},
        {"FEISHU_OAUTH_APP_SECRET": "oauth-secret"},
    ],
)
def test_settings_rejects_partial_dedicated_feishu_oauth_credentials(values):
    with pytest.raises(
        ValidationError,
        match="FEISHU_OAUTH_APP_ID and FEISHU_OAUTH_APP_SECRET",
    ):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ANONYMOUS_DEVICE_TTL_SECONDS", 0),
        ("USER_SESSION_SLIDING_TTL_SECONDS", 0),
        ("USER_SESSION_ABSOLUTE_TTL_SECONDS", 0),
        ("OAUTH_STATE_TTL_SECONDS", 0),
        ("USER_PUBLIC_BASE_URL", "ftp://172.18.26.1"),
    ],
)
def test_settings_rejects_invalid_user_auth_configuration(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_settings_rejects_user_session_sliding_ttl_beyond_absolute_ttl():
    with pytest.raises(ValidationError, match="sliding"):
        Settings(
            _env_file=None,
            USER_SESSION_SLIDING_TTL_SECONDS=31 * 24 * 3600,
            USER_SESSION_ABSOLUTE_TTL_SECONDS=30 * 24 * 3600,
        )
