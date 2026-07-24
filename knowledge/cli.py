import json
import logging
import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from knowledge.config.settings import get_settings
from knowledge.config.logging_config import configure_logging
from knowledge.repositories.vector_store_repository import VectorStoreRepository
from knowledge.services.ingestion_service import IngestionService
from knowledge.services.hybrid_rerank_service import HybridRerankService
from knowledge.services.keyword_retrieval_service import KeywordRetrievalService
from knowledge.services.multi_route_retrieval_service import MultiRouteRetrievalService
from knowledge.services.retrieval_service import RetrievalService
from knowledge.services.retrieval_pipeline_factory import (
    create_query_rewriter,
    create_reranker,
)
from knowledge.catalog.repository import CatalogRepository
from knowledge.migrations.legacy_catalog import LegacyCatalogMigrator
from knowledge.memory.index import MemoryIndex
from knowledge.memory.repository import MemoryRepository


app = typer.Typer(help="Knowledge ingestion and Chroma retrieval CLI.")
console = Console()
logger = logging.getLogger("knowledge.cli")


def _parse_where(where: Optional[str]) -> dict | None:
    if not where:
        return None
    try:
        parsed = json.loads(where)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--where must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("--where must be a JSON object")
    return parsed


def _scope_where(
    app_id: str,
    domain: Optional[str],
    name: Optional[str],
    additional_where: dict | None,
) -> dict:
    normalized_app_id = app_id.strip()
    if not normalized_app_id:
        raise typer.BadParameter("--app-id cannot be blank")
    scope = {
        key: value.strip()
        for key, value in {
            "app_id": normalized_app_id,
            "domain": domain,
            "name": name,
        }.items()
        if value and value.strip()
    }
    if not additional_where:
        return scope
    scope_clauses = [{key: value} for key, value in scope.items()]
    additional_clauses = (
        list(additional_where["$and"])
        if set(additional_where) == {"$and"}
        and isinstance(additional_where["$and"], list)
        else [additional_where]
    )
    return {"$and": [*scope_clauses, *additional_clauses]}


@app.command()
def ingest(
    app_id: str = typer.Option(..., "--app-id", help="Required application scope."),
    domain: Optional[str] = typer.Option(None, help="Optional domain metadata override."),
    name: Optional[str] = typer.Option(None, help="Optional knowledge name metadata."),
    reset: bool = typer.Option(
        False,
        help="Currently ignored to avoid accidentally deleting existing vectors.",
    ),
    dry_run: bool = typer.Option(False, help="Parse chunks without writing to Chroma."),
) -> None:
    started_at = perf_counter()
    logger.info(
        "Ingest started app_id=%r domain=%r name=%r dry_run=%s",
        app_id,
        domain,
        name,
        dry_run,
    )
    settings = get_settings()
    repository = None if dry_run else VectorStoreRepository.from_settings(settings)
    service = IngestionService(
        repository=repository,
        source_path=settings.knowledge_source_path,
        max_chunk_chars=settings.chunk_max_chars,
        overlap_chars=settings.chunk_overlap_chars,
        app_id=app_id,
        domain=domain,
        name=name,
    )
    summary = service.dry_run() if dry_run else service.ingest(reset=reset)
    logger.info(
        "Ingest completed app_id=%r chunk_count=%d parent_chunk_count=%d "
        "stored_count=%d dry_run=%s duration_ms=%.2f",
        app_id,
        summary.chunk_count,
        summary.parent_chunk_count,
        summary.stored_count,
        dry_run,
        (perf_counter() - started_at) * 1000,
    )
    console.print_json(
        data={
            "source_path": summary.source_path,
            "chunk_count": summary.chunk_count,
            "parent_chunk_count": summary.parent_chunk_count,
            "stored_count": summary.stored_count,
            "dry_run": dry_run,
        }
    )


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query."),
    app_id: str = typer.Option(..., "--app-id", help="Required application scope."),
    domain: Optional[str] = typer.Option(None, help="Optional domain scope."),
    name: Optional[str] = typer.Option(None, help="Optional knowledge name scope."),
    k: int = typer.Option(5, min=1, max=50, help="Number of results."),
    where: Optional[str] = typer.Option(None, help="Chroma metadata filter JSON."),
) -> None:
    started_at = perf_counter()
    logger.info(
        "Vector search started query=%r app_id=%r domain=%r name=%r k=%d",
        query,
        app_id,
        domain,
        name,
        k,
    )
    repository = VectorStoreRepository.from_settings()
    results = RetrievalService(repository).search(
        query=query,
        k=k,
        where=_scope_where(app_id, domain, name, _parse_where(where)),
    )
    logger.info(
        "Vector search completed query=%r app_id=%r result_count=%d duration_ms=%.2f",
        query,
        app_id,
        len(results),
        (perf_counter() - started_at) * 1000,
    )

    table = Table(title="Search Results")
    table.add_column("rank", justify="right")
    table.add_column("chunk_id")
    table.add_column("score")
    table.add_column("module")
    table.add_column("interface_type")
    table.add_column("preview")
    for index, result in enumerate(results, start=1):
        preview = result.content.replace("\n", " ")[:160]
        table.add_row(
            str(index),
            result.chunk_id,
            "" if result.score is None else f"{result.score:.4f}",
            str(result.metadata.get("module", "")),
            str(result.metadata.get("interface_type", "")),
            preview,
        )
    console.print(table)


@app.command("multi-search")
def multi_search(
    query: str = typer.Argument(..., help="Search query."),
    app_id: str = typer.Option(..., "--app-id", help="Required application scope."),
    domain: Optional[str] = typer.Option(None, help="Optional domain scope."),
    name: Optional[str] = typer.Option(None, help="Optional knowledge name scope."),
    keyword_k: int = typer.Option(20, min=1, max=100, help="Keyword candidate count."),
    vector_k: int = typer.Option(20, min=1, max=100, help="Vector candidate count."),
    final_k: int = typer.Option(5, min=1, max=50, help="Final result count."),
    where: Optional[str] = typer.Option(None, help="Chroma metadata filter JSON."),
) -> None:
    started_at = perf_counter()
    logger.info(
        "Multi-search started query=%r app_id=%r domain=%r name=%r "
        "keyword_k=%d vector_k=%d final_k=%d",
        query,
        app_id,
        domain,
        name,
        keyword_k,
        vector_k,
        final_k,
    )
    settings = get_settings()

    # multi-search 是完整多路召回入口，因此这里 require_embedding=True：
    # 后面 vector route 需要把用户 query 转成 embedding 后再查 Chroma。
    repository = VectorStoreRepository.from_settings(settings)

    # BM25 关键词召回服务。
    # 初始化时会读取当前 app_id/domain/name 范围内的 metadata，
    # 并在内存里构建 heading 与 bm25_keywords 两个 BM25 索引。
    keyword_service = KeywordRetrievalService(
        repository,
        app_id=app_id,
        domain=domain,
        name=name,
        title_weight=settings.bm25_title_weight,
        keywords_weight=settings.bm25_keywords_weight,
    )
    additional_where = _parse_where(where)

    # 这两个能力都是可选增强：
    # - query_rewriter 缺失时，直接使用原始 query 检索。
    # - reranker 缺失时，HybridRerankService 会使用 RRF 兜底排序。
    query_rewriter = create_query_rewriter(settings)
    reranker = create_reranker(settings)

    # 真正执行多路召回：
    # query rewrite -> BM25 route -> vector route -> merge/rerank。
    results = MultiRouteRetrievalService(
        repository,
        keyword_service,
        query_rewriter=query_rewriter,
        hybrid_ranker=HybridRerankService(reranker=reranker),
    ).search(
        query=query,
        keyword_k=keyword_k,
        vector_k=vector_k,
        final_k=final_k,
        where=additional_where,
    )
    logger.info(
        "Multi-search completed query=%r app_id=%r keyword_count=%d vector_count=%d "
        "final_count=%d rewrite_applied=%s rerank_applied=%s duration_ms=%.2f",
        query,
        app_id,
        len(results.keyword_results),
        len(results.vector_results),
        len(results.final_results),
        results.rewrite_applied,
        results.rerank_applied,
        (perf_counter() - started_at) * 1000,
    )

    # 先打印查询改写和开关状态，方便判断：
    # - 是否发生了 rewrite
    # - 是否真的需要检索
    # - rerank 是否实际生效
    console.print_json(
        data={
            "query": results.query,
            "retrieval_query": results.retrieval_query,
            "extracted_keywords": list(results.extracted_keywords),
            "retrieval_needed": results.retrieval_needed,
            "clarification_needed": results.clarification_needed,
            "rewrite_applied": results.rewrite_applied,
            "rerank_applied": results.rerank_applied,
        }
    )

    # 分开打印三张表，是为了让你能观察每一路召回的贡献：
    # Keyword Results：BM25 认为相关的候选
    # Vector Results：Chroma 向量认为相关的候选
    # Final Results：去重融合并重排后的最终候选
    console.print(_route_table("Keyword Results", results.keyword_results))
    console.print(_route_table("Vector Results", results.vector_results))
    console.print(_final_table(results.final_results))


def _route_table(title: str, results) -> Table:
    table = Table(title=title)
    table.add_column("rank", justify="right")
    table.add_column("chunk_id")
    table.add_column("score")
    table.add_column("score_type")
    table.add_column("module")
    table.add_column("preview")
    for result in results:
        direction = "higher" if result.higher_is_better else "lower"
        table.add_row(
            str(result.rank),
            result.chunk_id,
            f"{result.raw_score:.4f} ({direction})",
            result.score_type,
            str(result.metadata.get("module", "")),
            result.content.replace("\n", " ")[:160],
        )
    return table


def _final_table(results) -> Table:
    table = Table(title="Final Results")
    table.add_column("rank", justify="right")
    table.add_column("chunk_id")
    table.add_column("routes")
    table.add_column("rerank")
    table.add_column("rrf")
    table.add_column("preview")
    for result in results:
        table.add_row(
            str(result.rank),
            result.chunk_id,
            ",".join(result.retrieval_routes),
            "" if result.rerank_score is None else f"{result.rerank_score:.4f}",
            f"{result.fusion_score:.6f}",
            result.content.replace("\n", " ")[:160],
        )
    return table


@app.command()
def stats() -> None:
    started_at = perf_counter()
    logger.info("Stats started")
    settings = get_settings()
    repository = VectorStoreRepository.from_settings(settings, require_embedding=False)
    count = repository.count()
    logger.info(
        "Stats completed collection=%s count=%d duration_ms=%.2f",
        settings.chroma_collection_name,
        count,
        (perf_counter() - started_at) * 1000,
    )
    console.print_json(
        data={
            "collection_name": settings.chroma_collection_name,
            "vector_store_path": str(settings.vector_store_path),
            "count": count,
        }
    )


@app.command("hash-admin-password")
def hash_admin_password(
    password: str = typer.Option(
        ...,
        prompt="Admin password",
        hide_input=True,
        confirmation_prompt=True,
        help="Prompt for a password and print an Argon2 hash.",
    ),
) -> None:
    from pwdlib import PasswordHash

    typer.echo(PasswordHash.recommended().hash(password))


@app.command("migrate-legacy-catalog")
def migrate_legacy_catalog(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply metadata updates. Omit for a dry run.",
    ),
    backup_dir: Optional[Path] = typer.Option(
        None,
        help="Backup directory used before --apply.",
    ),
) -> None:
    settings = get_settings()
    if apply:
        destination = backup_dir or (
            settings.resolved_knowledge_storage_root
            / "backups"
            / datetime.now().strftime("%Y%m%d-%H%M%S")
        )
        destination.mkdir(parents=True, exist_ok=False)
        chroma_path = Path(settings.vector_store_path).expanduser().resolve()
        if chroma_path.exists():
            shutil.copytree(chroma_path, destination / "chroma")
        session_db = settings.resolved_agent_session_db
        if session_db.exists():
            shutil.copy2(session_db, destination / session_db.name)

    repository = VectorStoreRepository.from_settings(
        settings,
        require_embedding=False,
    )
    catalog = CatalogRepository(settings.resolved_knowledge_catalog_db)

    async def run_migration():
        await catalog.initialize()
        return await LegacyCatalogMigrator(
            catalog,
            repository.vector_store._collection,
        ).run(apply=apply)

    result = asyncio.run(run_migration())
    console.print_json(
        data={
            "mode": "apply" if apply else "dry-run",
            "candidate_count": result.candidate_count,
            "updated_count": result.updated_count,
            "source_id": result.source_id,
        }
    )


@app.command("memory-cleanup")
def memory_cleanup(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Permanently purge deleted/expired memories and terminal extraction jobs.",
    ),
) -> None:
    settings = get_settings()

    async def run_cleanup():
        repository = MemoryRepository(settings.resolved_memory_db)
        await repository.initialize()
        return await repository.cleanup_terminal_records(apply=apply)

    counts = asyncio.run(run_cleanup())
    console.print_json(data={
        "mode": "apply" if apply else "dry-run",
        **counts,
    })


@app.command("memory-rebuild-index")
def memory_rebuild_index(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Reset and rebuild only the separate confirmed-memory Chroma collection.",
    ),
) -> None:
    settings = get_settings()

    async def load_memories():
        repository = MemoryRepository(settings.resolved_memory_db)
        await repository.initialize()
        return await repository.list_memories(statuses=("confirmed",), limit=500)

    memories = asyncio.run(load_memories())
    if apply:
        vectors = VectorStoreRepository.from_settings(
            settings,
            collection_name=settings.memory_chroma_collection_name,
        )
        vectors.reset()
        index = MemoryIndex(vectors)
        for memory in memories:
            index.upsert(memory)
    console.print_json(data={
        "mode": "apply" if apply else "dry-run",
        "collection_name": settings.memory_chroma_collection_name,
        "confirmed_memory_count": len(memories),
        "rebuilt_count": len(memories) if apply else 0,
    })


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    try:
        app()
    except Exception:
        logger.exception("CLI command failed")
        raise


if __name__ == "__main__":
    main()
