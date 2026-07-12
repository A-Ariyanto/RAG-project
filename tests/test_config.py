"""Smoke test for Phase 0 config wiring."""

from app.config import Settings


def test_database_url_is_assembled_from_parts():
    s = Settings(
        postgres_user="u",
        postgres_password="p",
        postgres_db="d",
        postgres_host="h",
        postgres_port=1234,
    )
    assert s.database_url == "postgresql://u:p@h:1234/d"
