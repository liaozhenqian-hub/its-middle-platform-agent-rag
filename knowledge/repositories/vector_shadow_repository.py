from __future__ import annotations

from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass
from math import ceil
import logging
import random
from threading import Lock
from time import perf_counter
from typing import Any, Protocol
from uuid import uuid4
from psycopg import sql
from psycopg.types.json import Jsonb


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorShadowRecord:
    query_id: str
    primary_ids: tuple[str, ...]
    shadow_ids: tuple[str, ...]
    primary_latency_ms: float
    shadow_latency_ms: float
    top_k_overlap: float
    status: str


@dataclass(frozen=True)
class VectorShadowReport:
    sample_count: int
    primary_average_latency_ms: float
    primary_p90_latency_ms: float
    shadow_average_latency_ms: float
    shadow_p90_latency_ms: float
    average_top_k_overlap: float
    failure_rate: float


def summarize_shadow_records(records: list[VectorShadowRecord]) -> VectorShadowReport:
    if not records:
        return VectorShadowReport(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    primary = sorted(float(item.primary_latency_ms) for item in records)
    shadow = sorted(float(item.shadow_latency_ms) for item in records)
    return VectorShadowReport(
        sample_count=len(records),
        primary_average_latency_ms=sum(primary) / len(primary),
        primary_p90_latency_ms=_percentile(primary, 0.9),
        shadow_average_latency_ms=sum(shadow) / len(shadow),
        shadow_p90_latency_ms=_percentile(shadow, 0.9),
        average_top_k_overlap=(
            sum(float(item.top_k_overlap) for item in records) / len(records)
        ),
        failure_rate=(
            sum(item.status != "completed" for item in records) / len(records)
        ),
    )


def load_postgres_shadow_report(pool: Any, *, schema: str) -> VectorShadowReport:
    statement = sql.SQL(
        "SELECT primary_latency_ms, shadow_latency_ms, top_k_overlap, status "
        "FROM {}.{} ORDER BY created_at"
    ).format(sql.Identifier(schema), sql.Identifier("vector_shadow_comparisons"))
    with pool.connection() as connection:
        rows = connection.execute(statement).fetchall()
    controlled = [
        VectorShadowRecord("", (), (), row[0], row[1], row[2], row[3])
        for row in rows
    ]
    return summarize_shadow_records(controlled)


def _percentile(values: list[float], fraction: float) -> float:
    index = max(ceil(len(values) * fraction) - 1, 0)
    return values[index]


class VectorShadowAuditSink(Protocol):
    def record(self, record: VectorShadowRecord) -> None: ...


class InMemoryVectorShadowAudit:
    def __init__(self) -> None:
        self.records: list[VectorShadowRecord] = []
        self._lock = Lock()

    def record(self, record: VectorShadowRecord) -> None:
        with self._lock:
            self.records.append(record)


class LoggingVectorShadowAudit:
    def record(self, record: VectorShadowRecord) -> None:
        logger.info(
            "Vector shadow comparison query_id=%s status=%s primary_ids=%s "
            "shadow_ids=%s primary_ms=%.2f shadow_ms=%.2f overlap=%.4f",
            record.query_id,
            record.status,
            list(record.primary_ids),
            list(record.shadow_ids),
            record.primary_latency_ms,
            record.shadow_latency_ms,
            record.top_k_overlap,
        )


class PostgresVectorShadowAudit:
    def __init__(self, pool: Any, *, schema: str = "public") -> None:
        self.pool = pool
        self.table = sql.SQL("{}.{}").format(
            sql.Identifier(schema),
            sql.Identifier("vector_shadow_comparisons"),
        )

    def record(self, record: VectorShadowRecord) -> None:
        statement = sql.SQL(
            "INSERT INTO {} (id, primary_ids, shadow_ids, primary_latency_ms, "
            "shadow_latency_ms, top_k_overlap, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)"
        ).format(self.table)
        with self.pool.connection() as connection:
            connection.execute(
                statement,
                (
                    record.query_id,
                    Jsonb(list(record.primary_ids)),
                    Jsonb(list(record.shadow_ids)),
                    record.primary_latency_ms,
                    record.shadow_latency_ms,
                    record.top_k_overlap,
                    record.status,
                ),
            )


class ShadowVectorStoreRepository:
    """Read from the primary repository and compare a sampled shadow asynchronously."""

    def __init__(
        self,
        primary: Any,
        shadow: Any,
        *,
        audit_sink: VectorShadowAuditSink,
        sample_rate: float = 1.0,
        executor: Executor | None = None,
    ) -> None:
        if not 0 <= sample_rate <= 1:
            raise ValueError("sample_rate must be between 0 and 1")
        self.primary = primary
        self.shadow = shadow
        self.audit_sink = audit_sink
        self.sample_rate = sample_rate
        self._owned_executor = executor is None
        self.executor = executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="vector-shadow",
        )

    def search(self, query: str, k: int = 5, where: dict[str, Any] | None = None):
        primary_started = perf_counter()
        primary_results = self.primary.search(query, k=k, where=where)
        primary_latency_ms = (perf_counter() - primary_started) * 1000
        if self.sample_rate > 0 and random.random() <= self.sample_rate:
            query_id = str(uuid4())
            primary_ids = tuple(item.chunk_id for item in primary_results)
            shadow_started = perf_counter()
            future = self.executor.submit(self.shadow.search, query, k, where)
            future.add_done_callback(
                lambda completed: self._record_shadow(
                    query_id=query_id,
                    primary_ids=primary_ids,
                    primary_latency_ms=primary_latency_ms,
                    shadow_started=shadow_started,
                    future=completed,
                )
            )
        return primary_results

    def _record_shadow(
        self,
        *,
        query_id: str,
        primary_ids: tuple[str, ...],
        primary_latency_ms: float,
        shadow_started: float,
        future: Future,
    ) -> None:
        shadow_latency_ms = (perf_counter() - shadow_started) * 1000
        try:
            shadow_ids = tuple(item.chunk_id for item in future.result())
            denominator = max(len(primary_ids), 1)
            overlap = len(set(primary_ids) & set(shadow_ids)) / denominator
            status = "completed"
        except Exception:
            shadow_ids = ()
            overlap = 0.0
            status = "failed"
        self.audit_sink.record(
            VectorShadowRecord(
                query_id=query_id,
                primary_ids=primary_ids,
                shadow_ids=shadow_ids,
                primary_latency_ms=primary_latency_ms,
                shadow_latency_ms=shadow_latency_ms,
                top_k_overlap=overlap,
                status=status,
            )
        )

    def close(self) -> None:
        if self._owned_executor:
            self.executor.shutdown(wait=False, cancel_futures=True)
        close = getattr(self.shadow, "close", None)
        if callable(close):
            close()

    def __getattr__(self, name: str):
        return getattr(self.primary, name)
