from typer.testing import CliRunner
from pwdlib import PasswordHash

from knowledge import cli
from knowledge.schemas.documents import KeywordIndexRecord, KnowledgeChunk, SearchResult


class CliRepository:
    chunk = KnowledgeChunk(
        chunk_id="keyword-sdk",
        heading="SDK 查询",
        content="关键词召回正文",
        metadata={
            "chunk_id": "keyword-sdk",
            "heading": "SDK 查询",
            "bm25_keywords": "SDK, getDataV2",
            "app_id": "middle-platform",
            "domain": "指标平台",
            "name": "指标平台",
        },
    )

    def get_keyword_index_records(self, where=None):
        return [
            KeywordIndexRecord(
                chunk_id=self.chunk.chunk_id,
                heading=self.chunk.heading,
                keywords=str(self.chunk.metadata["bm25_keywords"]),
                metadata=dict(self.chunk.metadata),
            )
        ]

    def get_chunk_ids(self, where=None):
        return {self.chunk.chunk_id}

    def get_chunks(self, where=None, ids=None):
        return [self.chunk]

    def search(self, query, k=5, where=None):
        return [
            SearchResult(
                chunk_id="vector-sdk",
                content="向量召回正文",
                metadata={"heading": "SDK 开放接口", "app_id": "middle-platform"},
                score=0.2,
            )
        ]


def test_multi_search_cli_requires_app_id():
    result = CliRunner().invoke(cli.app, ["multi-search", "SDK 怎么查询"])

    assert result.exit_code == 2
    assert "--app-id" in result.output + result.stderr


def test_vector_search_cli_requires_app_id():
    result = CliRunner().invoke(cli.app, ["search", "SDK 怎么查询"])

    assert result.exit_code == 2
    assert "--app-id" in result.output + result.stderr


def test_multi_search_cli_displays_routes_separately(monkeypatch, caplog):
    monkeypatch.setattr(
        cli,
        "create_vector_store_repository",
        lambda *args, **kwargs: CliRepository(),
    )
    monkeypatch.setattr(cli, "create_query_rewriter", lambda settings: None, raising=False)
    monkeypatch.setattr(cli, "create_reranker", lambda settings: None, raising=False)

    with caplog.at_level("INFO", logger="knowledge.cli"):
        result = CliRunner().invoke(
            cli.app,
            [
                "multi-search",
                "SDK 怎么查询",
                "--app-id",
                "middle-platform",
                "--domain",
                "指标平台",
                "--keyword-k",
                "3",
                "--vector-k",
                "4",
            ],
        )

    assert result.exit_code == 0
    assert "Keyword Results" in result.stdout
    assert "Vector Results" in result.stdout
    assert "Final Results" in result.stdout
    assert "retrieval_query" in result.stdout
    assert "keyword-sdk" in result.stdout
    assert "vector-sdk" in result.stdout
    assert "Multi-search started" in caplog.text
    assert "query='SDK 怎么查询'" in caplog.text
    assert "keyword_count=1" in caplog.text
    assert "vector_count=1" in caplog.text
    assert "final_count=2" in caplog.text
    assert "rerank_applied=False" in caplog.text
    assert "关键词召回正文" not in caplog.text
    assert "向量召回正文" not in caplog.text


def test_hash_admin_password_prompts_and_emits_argon2_hash():
    result = CliRunner().invoke(
        cli.app,
        ["hash-admin-password"],
        input="correct horse battery staple\ncorrect horse battery staple\n",
    )

    assert result.exit_code == 0
    password_hash = next(
        line.strip() for line in result.stdout.splitlines() if line.startswith("$argon2")
    )
    assert PasswordHash.recommended().verify(
        "correct horse battery staple", password_hash
    )
