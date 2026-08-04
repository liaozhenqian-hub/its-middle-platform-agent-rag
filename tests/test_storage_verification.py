from knowledge.migrations.verification import (
    RelationalTableSnapshot,
    verify_relational_snapshots,
    verify_vector_repositories,
    _digest,
)
from datetime import datetime, timezone
import sqlite3
from types import SimpleNamespace
from knowledge.migrations.verification import capture_sqlite_relational_state
from knowledge.schemas.documents import KnowledgeChunk


class _Repository:
    def __init__(self, chunks):
        self.chunks = {chunk.chunk_id: chunk for chunk in chunks}

    def count(self):
        return len(self.chunks)

    def get_chunk_ids(self, where=None):
        return set(self.chunks)

    def get_chunks(self, ids=None, where=None):
        selected = ids if ids is not None else sorted(self.chunks)
        return [self.chunks[item] for item in selected if item in self.chunks]


def _chunk(chunk_id, content, **metadata):
    return KnowledgeChunk(
        chunk_id=chunk_id,
        heading=chunk_id,
        content=content,
        metadata=metadata,
    )


def test_vector_verification_checks_count_ids_and_sample_hashes_without_content():
    source = _Repository([_chunk("a", "alpha", branch="develop"), _chunk("b", "beta")])
    target = _Repository([_chunk("a", "alpha", branch="develop"), _chunk("b", "changed")])

    report = verify_vector_repositories(source, target, sample_size=10)

    assert report.count_matches is True
    assert report.id_set_matches is True
    assert report.sampled_count == 2
    assert report.hash_mismatch_count == 1
    assert report.passed is False
    assert "alpha" not in repr(report)
    assert "beta" not in repr(report)


def test_relational_verification_checks_ids_status_foreign_keys_and_hashes_safely():
    source = {
        "jobs": RelationalTableSnapshot(
            count=2,
            primary_key_digests=frozenset({"pk-a", "pk-b"}),
            status_counts={"queued": 1, "completed": 1},
            sampled_row_digests={"pk-a": "row-a", "pk-b": "row-b"},
            foreign_key_violation_count=0,
        )
    }
    target = {
        "jobs": RelationalTableSnapshot(
            count=2,
            primary_key_digests=frozenset({"pk-a", "pk-b"}),
            status_counts={"queued": 2},
            sampled_row_digests={"pk-a": "row-a", "pk-b": "changed"},
            foreign_key_violation_count=1,
        )
    }

    report = verify_relational_snapshots(source, target)

    assert report.passed is False
    assert report.table_count == 1
    assert report.count_mismatch_count == 0
    assert report.primary_key_mismatch_count == 0
    assert report.status_mismatch_count == 1
    assert report.hash_mismatch_count == 1
    assert report.foreign_key_violation_count == 1
    serialized = repr(report)
    assert "pk-a" not in serialized
    assert "row-a" not in serialized
    assert "changed" not in serialized


def test_relational_verification_reports_missing_tables_without_identifiers():
    snapshot = RelationalTableSnapshot(
        count=0,
        primary_key_digests=frozenset(),
        status_counts={},
        sampled_row_digests={},
        foreign_key_violation_count=0,
    )

    report = verify_relational_snapshots({"source_only": snapshot}, {"target_only": snapshot})

    assert report.missing_target_table_count == 1
    assert report.extra_target_table_count == 1
    assert report.passed is False
    assert "source_only" not in repr(report)
    assert "target_only" not in repr(report)


def test_relational_hash_normalizes_sqlite_iso_timestamps_to_timestamptz():
    assert _digest({"created_at": "2026-07-28T01:02:03+00:00"}) == _digest(
        {"created_at": datetime(2026, 7, 28, 1, 2, 3, tzinfo=timezone.utc)}
    )


def test_sqlite_relational_state_keeps_sample_keys_internal_to_database_adapter(tmp_path):
    database = tmp_path / "source.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT,payload TEXT)")
        connection.executemany(
            "INSERT INTO jobs VALUES(?,?,?)",
            [("a", "queued", "private-a"), ("b", "completed", "private-b")],
        )
    plan = SimpleNamespace(
        source_path=database,
        table="jobs",
        column_map={},
        json_columns=set(),
        boolean_columns=set(),
    )

    snapshots, sample_keys = capture_sqlite_relational_state(
        [plan], primary_keys={"jobs": ("id",)}, sample_size=1
    )

    assert snapshots["jobs"].count == 2
    assert len(sample_keys["jobs"]) == 1
    assert "private-a" not in repr(snapshots)
    assert "private-b" not in repr(snapshots)
    assert _digest({"created_at": "2026-07-28T01:02:03"}) == _digest(
        {"created_at": datetime(2026, 7, 28, 1, 2, 3, tzinfo=timezone.utc)}
    )
    assert _digest({"created_at": "2026-07-28 01:02:03"}) == _digest(
        {"created_at": datetime(2026, 7, 28, 1, 2, 3, tzinfo=timezone.utc)}
    )
