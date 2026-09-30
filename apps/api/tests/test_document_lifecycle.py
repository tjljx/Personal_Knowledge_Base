"""Document trash, version rollback, and legacy Word conversion."""

import subprocess
import zipfile
from pathlib import Path

from conftest import add_user, login
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api.config import get_settings
from knowledge_api.document_lifecycle import reconcile_next_projection
from knowledge_api.models import (
    DocumentChunk,
    DocumentChunkEmbedding,
    DocumentVersion,
    GraphEntity,
    GraphEvidence,
    VectorProjectionJob,
)
from knowledge_api.parsers import parse_file
from knowledge_api.worker import process_next


def test_trash_restore_rollback_and_projection(client, tmp_path, monkeypatch):
    http, engine = client
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "vector_store_url", "http://qdrant.invalid")
    monkeypatch.setattr(settings, "embedding_model", "test-embedding")
    add_user(engine, "owner")
    viewer_id = add_user(engine, "viewer")
    owner = login(http, "owner")
    viewer = login(http, "viewer")
    kb_id = http.post("/api/v1/knowledge-bases", headers=owner, json={"name": "Lifecycle"}).json()[
        "id"
    ]
    http.post(
        f"/api/v1/knowledge-bases/{kb_id}/members",
        headers=owner,
        json={"user_id": viewer_id, "member_role": "VIEWER"},
    )
    base = f"/api/v1/knowledge-bases/{kb_id}/documents"
    doc = http.post(base, headers=owner, files={"file": ("first.md", b"Alpha builds Beta")}).json()
    path = f"{base}/{doc['id']}"
    with Session(engine) as db:
        assert process_next(db)
        version1 = db.scalar(
            select(DocumentVersion).where(DocumentVersion.document_id == doc["id"])
        )
        chunk1 = db.scalar(
            select(DocumentChunk).where(DocumentChunk.document_version_id == version1.id)
        )
        db.add(
            DocumentChunkEmbedding(
                chunk_id=chunk1.id, model=settings.embedding_model, vector=[0.1, 0.2]
            )
        )
        source = GraphEntity(
            knowledge_base_id=kb_id, name="Alpha", normalized_name="alpha", entity_type="组织"
        )
        target = GraphEntity(
            knowledge_base_id=kb_id, name="Beta", normalized_name="beta", entity_type="产品"
        )
        db.add_all([source, target])
        db.flush()
        db.add(
            GraphEvidence(
                knowledge_base_id=kb_id,
                source_entity_id=source.id,
                target_entity_id=target.id,
                relation_type="builds",
                document_version_id=version1.id,
                chunk_id=chunk1.id,
                quote="Alpha builds Beta",
                confidence=0.75,
                model="test",
            )
        )
        db.commit()
        first_chunk_id = chunk1.id
    assert http.get(f"{base}/{doc['id']}/versions/1/download", headers=owner).status_code == 200
    assert http.get(f"/api/v1/knowledge-bases/{kb_id}/search/chunks?q=Alpha", headers=owner).json()
    assert http.get(f"/api/v1/knowledge-bases/{kb_id}/knowledge-graph", headers=owner).json()[
        "edges"
    ]
    assert http.delete(path, headers=viewer).status_code == 403
    assert http.delete(path, headers=owner).status_code == 204
    assert http.get(path, headers=owner).status_code == 404
    assert http.get(base, headers=owner).json() == []
    assert http.get(base, headers=viewer, params={"deleted": True}).status_code == 403
    assert http.get(base, headers=owner, params={"deleted": True}).json()[0]["id"] == doc["id"]
    assert (
        http.get(f"/api/v1/knowledge-bases/{kb_id}/search/chunks?q=Alpha", headers=owner).json()
        == []
    )
    assert http.get(f"/api/v1/knowledge-bases/{kb_id}/search?q=Alpha", headers=owner).json() == []
    assert (
        http.get(f"/api/v1/knowledge-bases/{kb_id}/knowledge-graph", headers=owner).json()["edges"]
        == []
    )
    assert http.get(f"{path}/versions/1/download", headers=owner).status_code == 404

    calls = []
    monkeypatch.setattr(
        "knowledge_api.document_lifecycle.upsert_vectors",
        lambda _settings, points: calls.append(("upsert", [point["id"] for point in points])),
    )
    monkeypatch.setattr(
        "knowledge_api.document_lifecycle.delete_vectors",
        lambda _settings, ids: calls.append(("delete", ids)),
    )
    with Session(engine) as db:
        assert reconcile_next_projection(db)
        assert not db.scalars(select(VectorProjectionJob)).all()
    assert ("delete", [first_chunk_id]) in calls

    assert http.post(f"{path}/restore", headers=owner).status_code == 200
    assert http.get(f"/api/v1/knowledge-bases/{kb_id}/search/chunks?q=Alpha", headers=owner).json()
    with Session(engine) as db:
        assert reconcile_next_projection(db)
    assert ("upsert", [first_chunk_id]) in calls

    second = http.post(
        f"{path}/versions", headers=owner, files={"file": ("second.md", b"Gamma builds Delta")}
    )
    assert second.status_code == 201
    assert second.json()["version_no"] == 2
    assert (
        http.get(f"/api/v1/knowledge-bases/{kb_id}/search/chunks?q=Alpha", headers=owner).json()
        == []
    )
    assert (
        http.get(f"/api/v1/knowledge-bases/{kb_id}/knowledge-graph", headers=owner).json()["edges"]
        == []
    )
    rolled = http.post(f"{path}/versions/1/rollback", headers=owner)
    assert rolled.status_code == 200
    assert rolled.json()["current_version"] == 1
    assert http.get(f"/api/v1/knowledge-bases/{kb_id}/search/chunks?q=Alpha", headers=owner).json()
    assert http.get(f"/api/v1/knowledge-bases/{kb_id}/knowledge-graph", headers=owner).json()[
        "edges"
    ]
    third = http.post(
        f"{path}/versions", headers=owner, files={"file": ("third.md", b"Third unique version")}
    )
    assert third.status_code == 201
    assert third.json()["version_no"] == 3


def test_legacy_doc_conversion_preserves_original_and_parses_text(tmp_path, monkeypatch):
    source = tmp_path / "hash-named-original"
    source.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"doc data")

    def convert(args, **_kwargs):
        assert Path(args[-1]).suffix == ".doc"
        assert Path(args[-1]).read_bytes() == source.read_bytes()
        converted = Path(args[args.index("--outdir") + 1]) / "source.docx"
        with zipfile.ZipFile(converted, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="urn:w"><w:p><w:t>Legacy Word content</w:t></w:p></w:document>',
            )
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("knowledge_api.parsers.subprocess.run", convert)
    monkeypatch.setattr("knowledge_api.parsers._render_word_pages", lambda *_: None)
    output = parse_file(source, "legacy.doc")
    assert output.parser_name == "libreoffice-docx"
    assert "Legacy Word content" in output.markdown
    assert output.structure["parser_config"]["converted_from"] == "doc"
    assert source.read_bytes().startswith(bytes.fromhex("d0cf11e0a1b11ae1"))
