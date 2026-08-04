from urllib.parse import parse_qs, urlsplit

from knowledge.persistence.postgres_urls import postgres_saver_url


def test_postgres_saver_url_adds_controlled_search_path_without_exposing_password():
    value = postgres_saver_url(
        "postgresql://user:p%40ss@db.internal:5432/middle_agent?sslmode=disable",
        schema="agent_test",
    )

    parsed = urlsplit(value)
    assert parse_qs(parsed.query)["options"] == ["-csearch_path=agent_test,public"]
    assert "p@ss" not in value
