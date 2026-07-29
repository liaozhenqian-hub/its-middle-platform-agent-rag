import json
import sqlite3

from knowledge.migrations.enrich_critical_eval_cases import enrich
from knowledge.quality.critical_cases import (
    CRITICAL_CASE_DEFINITIONS,
    OFFICIAL_CRITICAL_CASE_IDS,
    RESERVE_CRITICAL_CASE_IDS,
)


def _create_db(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE eval_cases (
            id TEXT PRIMARY KEY, tags_json TEXT NOT NULL DEFAULT '[]',
            required_tools_json TEXT NOT NULL DEFAULT '[]',
            required_citation_types_json TEXT NOT NULL DEFAULT '[]',
            required_facts_json TEXT NOT NULL DEFAULT '[]',
            forbidden_facts_json TEXT NOT NULL DEFAULT '[]',
            task_type TEXT NOT NULL DEFAULT 'unknown',
            suite TEXT NOT NULL DEFAULT 'routing-breadth',
            max_tool_calls INTEGER NOT NULL DEFAULT 4,
            expected_behavior TEXT NOT NULL DEFAULT 'answer',
            priority TEXT NOT NULL DEFAULT 'critical', enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    for case_id in (*OFFICIAL_CRITICAL_CASE_IDS, *RESERVE_CRITICAL_CASE_IDS):
        connection.execute("INSERT INTO eval_cases(id) VALUES (?)", (case_id,))
    connection.commit()
    connection.close()


def test_official_suite_is_exactly_30_and_each_case_has_constraints():
    assert len(OFFICIAL_CRITICAL_CASE_IDS) == 30
    assert not set(OFFICIAL_CRITICAL_CASE_IDS) & set(RESERVE_CRITICAL_CASE_IDS)
    assert set(OFFICIAL_CRITICAL_CASE_IDS) == set(CRITICAL_CASE_DEFINITIONS)
    for case_id in OFFICIAL_CRITICAL_CASE_IDS:
        definition = CRITICAL_CASE_DEFINITIONS[case_id]
        assert definition["facts"]
        if case_id not in {
            "write-delete-metric", "no-release-evidence", "no-swagger-evidence"
        }:
            assert definition["citations"]


def test_enrich_critical_cases_is_idempotent_and_dry_run_does_not_mutate(tmp_path):
    path = tmp_path / "quality.db"
    _create_db(path)
    before = sqlite3.connect(path).execute(
        "SELECT required_facts_json FROM eval_cases WHERE id=?",
        (OFFICIAL_CRITICAL_CASE_IDS[0],),
    ).fetchone()[0]
    result = enrich(path, apply=False)
    assert result["official_count"] == 30
    assert sqlite3.connect(path).execute(
        "SELECT required_facts_json FROM eval_cases WHERE id=?",
        (OFFICIAL_CRITICAL_CASE_IDS[0],),
    ).fetchone()[0] == before

    enrich(path, apply=True)
    enrich(path, apply=True)
    connection = sqlite3.connect(path)
    rows = connection.execute(
        "SELECT id, suite, tags_json, required_facts_json, required_citation_types_json FROM eval_cases"
    ).fetchall()
    connection.close()
    official = {row[0]: row for row in rows if row[1] == "critical-v2"}
    assert len(official) == 30
    for case_id, row in official.items():
        assert "official-critical-v2" in json.loads(row[2])
        assert json.loads(row[3])
        if case_id not in {
            "write-delete-metric", "no-release-evidence", "no-swagger-evidence"
        }:
            assert json.loads(row[4])
