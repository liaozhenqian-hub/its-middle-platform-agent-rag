from knowledge.persistence.sqlite_compat import translate_sqlite_statement


def test_sqlite_placeholders_and_begin_immediate_are_translated():
    statement, parameters = translate_sqlite_statement(
        "SELECT * FROM records WHERE owner_id=? AND status=?",
        ("user-1", "active"),
    )
    assert statement == "SELECT * FROM records WHERE owner_id=:p0 AND status=:p1"
    assert parameters == {"p0": "user-1", "p1": "active"}
    assert translate_sqlite_statement("BEGIN IMMEDIATE", ())[0] == "SELECT 1"


def test_insert_or_ignore_becomes_postgres_conflict_clause():
    statement, _ = translate_sqlite_statement(
        "INSERT OR IGNORE INTO jobs(id,status) VALUES(?,?)",
        ("job-1", "queued"),
    )
    assert statement == (
        "INSERT INTO jobs(id,status) VALUES(:p0,:p1) ON CONFLICT DO NOTHING"
    )


def test_sqlite_integer_booleans_are_coerced_for_postgres_boolean_columns():
    statement, parameters = translate_sqlite_statement(
        "UPDATE memory_conflicts SET resolved=? WHERE resolved=0 AND id=?",
        (1, "conflict-1"),
    )
    assert "resolved=FALSE" in statement
    assert parameters["p0"] is True
    inserted, _ = translate_sqlite_statement(
        "INSERT INTO eval_runs(id,cancel_requested) VALUES(?,0)",
        ("run-1",),
    )
    assert "VALUES(:p0,FALSE)" in inserted
