"""A usable backup includes a consistent database copy and every referenced blob."""

import hashlib
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from knowledge_api.backup import create_backup, verify_backup
from knowledge_api.config import get_settings
from knowledge_api.db import Base
from knowledge_api.models import Document, DocumentVersion, KnowledgeBase, User


def test_sqlite_backup_verifies_database_and_blob(tmp_path):
    database = tmp_path / "live.sqlite"
    storage = tmp_path / "files"
    settings = get_settings().model_copy(
        update={"database_url": f"sqlite:///{database.as_posix()}", "storage_root": storage}
    )
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    content = b"# Document\n\nBackup evidence"
    digest = hashlib.sha256(content).hexdigest()
    blob = storage / "blobs" / digest[:2] / digest
    blob.parent.mkdir(parents=True)
    blob.write_bytes(content)
    with Session(engine) as db:
        user = User(username="owner", password_hash="hash")
        db.add(user)
        db.flush()
        kb = KnowledgeBase(name="KB", owner_id=user.id)
        db.add(kb)
        db.flush()
        document = Document(
            knowledge_base_id=kb.id,
            title="Document",
            file_type="md",
            created_by=user.id,
        )
        db.add(document)
        db.flush()
        db.add(
            DocumentVersion(
                document_id=document.id,
                version_no=1,
                original_filename="note.md",
                content_type="text/markdown",
                sha256=digest,
                size_bytes=len(content),
                created_by=user.id,
            )
        )
        db.commit()
    destination = tmp_path / "backup"
    manifest = create_backup(settings, destination, engine)
    assert manifest["blobs"] == {digest: len(content)}
    assert verify_backup(destination) == manifest
    with sqlite3.connect(destination / "database.dump") as restored:
        assert restored.execute("SELECT sha256 FROM document_versions").fetchone()[0] == digest
    (destination / "blobs" / digest[:2] / digest).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="原文件备份校验失败"):
        verify_backup(destination)
    engine.dispose()
