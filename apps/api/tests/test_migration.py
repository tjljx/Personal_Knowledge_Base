from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from knowledge_api.config import get_settings


def test_initial_migration_creates_schema(tmp_path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'migration.sqlite').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        config = Config("apps/api/alembic.ini")
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        try:
            assert {
                "users",
                "knowledge_bases",
                "knowledge_base_members",
                "login_attempts",
                "revoked_tokens",
                "auth_sessions",
                "worker_heartbeats",
                "audit_logs",
                "documents",
                "document_versions",
                "document_tags",
                "document_parse_jobs",
                "document_parse_results",
                "document_chunks",
                "document_chunk_embeddings",
                "cloud_budget_months",
                "cloud_model_usage",
                "chat_sessions",
                "chat_messages",
            }.issubset(inspect(engine).get_table_names())
            assert "auth_version" in {
                column["name"] for column in inspect(engine).get_columns("users")
            }
        finally:
            engine.dispose()
        command.check(config)
    finally:
        get_settings.cache_clear()


def test_metadata_migration_preserves_existing_documents(tmp_path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'existing.sqlite').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        config = Config("apps/api/alembic.ini")
        command.upgrade(config, "0002_documents")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users (id, username, password_hash, role, status, created_at) "
                        "VALUES ('11111111111111111111111111111111', 'owner', 'hash', 'ADMIN', 'ACTIVE', '2026-01-01')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO knowledge_bases (id, name, description, owner_id, status, created_at) "
                        "VALUES ('22222222222222222222222222222222', 'KB', '', '11111111111111111111111111111111', 'ACTIVE', '2026-01-01')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO documents (id, knowledge_base_id, title, current_version, status, created_by, created_at) "
                        "VALUES ('33333333333333333333333333333333', '22222222222222222222222222222222', 'old.md', 1, 'UPLOADED', '11111111111111111111111111111111', '2026-01-01')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO document_versions (id, document_id, version_no, original_filename, content_type, sha256, size_bytes, status, created_by, created_at) "
                        "VALUES ('44444444444444444444444444444444', '33333333333333333333333333333333', 1, 'old.md', 'text/markdown', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 3, 'UPLOADED', '11111111111111111111111111111111', '2026-01-01')"
                    )
                )
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT auth_version FROM users WHERE username = 'owner'")
                    ).scalar_one()
                    == 0
                )
                row = connection.execute(
                    text(
                        "SELECT title, file_type, updated_at FROM documents WHERE id = '33333333333333333333333333333333'"
                    )
                ).one()
                assert row.title == "old.md"
                assert row.file_type == "md"
                assert str(row.updated_at).startswith("2026-01-01")
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()
