from knowledge.config.settings import Settings
from knowledge.services import retrieval_pipeline_factory


class CapturingOpenAI:
    calls = []

    def __init__(self, **kwargs):
        self.calls.append(kwargs)


def test_factory_disables_optional_models_without_api_keys(monkeypatch):
    monkeypatch.setattr(retrieval_pipeline_factory, "OpenAI", CapturingOpenAI)
    settings = Settings(
        _env_file=None,
        EMBEDDING_API_KEY="",
        DASHSCOPE_API_KEY="",
        DEEPSEEK_API_KEY="",
        RERANK_API_KEY="",
        OPENAI_API_KEY="",
    )

    assert retrieval_pipeline_factory.create_query_rewriter(settings) is None
    assert retrieval_pipeline_factory.create_reranker(settings) is None


def test_factory_builds_deepseek_rewriter_and_qwen_reranker(monkeypatch):
    CapturingOpenAI.calls.clear()
    monkeypatch.setattr(retrieval_pipeline_factory, "OpenAI", CapturingOpenAI)
    settings = Settings(
        _env_file=None,
        DEEPSEEK_API_KEY="deepseek-key",
        DEEPSEEK_BASE_URL="https://api.deepseek.com",
        DEEPSEEK_CHAT_MODEL="deepseek-v4-flash",
        DASHSCOPE_API_KEY="dashscope-key",
    )

    rewriter = retrieval_pipeline_factory.create_query_rewriter(settings)
    reranker = retrieval_pipeline_factory.create_reranker(settings)

    assert rewriter.model == "deepseek-v4-flash"
    assert reranker.model == "qwen3-rerank"
    assert CapturingOpenAI.calls == [
        {
            "api_key": "deepseek-key",
            "base_url": "https://api.deepseek.com",
            "timeout": 15.0,
        },
        {
            "api_key": "dashscope-key",
            "base_url": "https://dashscope.aliyuncs.com/compatible-api/v1",
            "timeout": 20.0,
        },
    ]
