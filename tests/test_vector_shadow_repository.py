from concurrent.futures import Future

from knowledge.repositories.vector_shadow_repository import (
    InMemoryVectorShadowAudit,
    PostgresVectorShadowAudit,
    ShadowVectorStoreRepository,
    VectorShadowRecord,
    summarize_shadow_records,
)
from knowledge.schemas.documents import SearchResult


class _ImmediateExecutor:
    def submit(self, function, *args, **kwargs):
        future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future


class _Repository:
    def __init__(self, ids):
        self.ids = ids
        self.calls = []

    def search(self, query, k=5, where=None):
        self.calls.append((query, k, where))
        return [
            SearchResult(chunk_id=item, content="not audited", metadata={}, score=0.1)
            for item in self.ids[:k]
        ]

    def count(self):
        return len(self.ids)


def test_shadow_never_changes_primary_results_and_audits_only_ids():
    primary = _Repository(["a", "b", "c"])
    shadow = _Repository(["b", "a", "d"])
    audit = InMemoryVectorShadowAudit()
    repository = ShadowVectorStoreRepository(
        primary,
        shadow,
        audit_sink=audit,
        sample_rate=1.0,
        executor=_ImmediateExecutor(),
    )

    results = repository.search("sensitive question", k=3, where={"domain": "approval"})

    assert [result.chunk_id for result in results] == ["a", "b", "c"]
    assert audit.records[0].primary_ids == ("a", "b", "c")
    assert audit.records[0].shadow_ids == ("b", "a", "d")
    assert audit.records[0].top_k_overlap == 2 / 3
    assert not hasattr(audit.records[0], "query")
    assert "not audited" not in repr(audit.records[0])


def test_shadow_failure_is_fail_open_and_delegates_non_search_calls():
    primary = _Repository(["a"])

    class BrokenShadow(_Repository):
        def search(self, query, k=5, where=None):
            raise RuntimeError("unavailable")

    audit = InMemoryVectorShadowAudit()
    repository = ShadowVectorStoreRepository(
        primary,
        BrokenShadow([]),
        audit_sink=audit,
        sample_rate=1.0,
        executor=_ImmediateExecutor(),
    )

    assert [item.chunk_id for item in repository.search("query")] == ["a"]
    assert repository.count() == 1
    assert audit.records[0].status == "failed"


def test_postgres_shadow_audit_writes_only_controlled_fields():
    captured = []

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, params): captured.append((str(statement), params))

    class Pool:
        def connection(self): return Connection()

    sink = PostgresVectorShadowAudit(Pool(), schema="public")
    sink.record(VectorShadowRecord(
        query_id="query-1",
        primary_ids=("a",),
        shadow_ids=("b",),
        primary_latency_ms=1.0,
        shadow_latency_ms=2.0,
        top_k_overlap=0.0,
        status="completed",
    ))

    serialized = repr(captured)
    assert "query-1" in serialized
    assert "question text" not in serialized
    assert "embedding" not in serialized.casefold()


def test_shadow_report_aggregates_latency_overlap_and_failures_without_ids():
    records = [
        VectorShadowRecord("q1", ("secret-a",), ("secret-a",), 10, 20, 1.0, "completed"),
        VectorShadowRecord("q2", ("secret-b",), (), 30, 80, 0.0, "failed"),
    ]

    report = summarize_shadow_records(records)

    assert report.sample_count == 2
    assert report.primary_average_latency_ms == 20
    assert report.primary_p90_latency_ms == 30
    assert report.shadow_average_latency_ms == 50
    assert report.shadow_p90_latency_ms == 80
    assert report.average_top_k_overlap == 0.5
    assert report.failure_rate == 0.5
    assert "secret-a" not in repr(report)
