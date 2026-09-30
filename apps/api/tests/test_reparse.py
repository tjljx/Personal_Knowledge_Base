"""Visible parse gaps and explicit, repeatable document reparsing."""

from io import BytesIO

from conftest import add_user, login
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api.config import get_settings
from knowledge_api.document_lifecycle import reconcile_next_projection
from knowledge_api.models import DocumentChunk, DocumentParseJob, VectorDeletionJob
from knowledge_api.worker import process_next


def test_parse_overview_and_force_reparse_replaces_chunks(client, tmp_path, monkeypatch):
    http, engine = client
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "vector_store_url", "http://qdrant.invalid")
    add_user(engine, "owner")
    viewer_id = add_user(engine, "viewer")
    owner = login(http, "owner")
    viewer = login(http, "viewer")
    kb_id = http.post("/api/v1/knowledge-bases", headers=owner, json={"name": "Reparse"}).json()[
        "id"
    ]
    http.post(
        f"/api/v1/knowledge-bases/{kb_id}/members",
        headers=owner,
        json={"user_id": viewer_id, "member_role": "VIEWER"},
    )
    base = f"/api/v1/knowledge-bases/{kb_id}/documents"
    uploaded = http.post(
        base, headers=owner, files={"file": ("guide.md", b"Alpha builds Beta")}
    ).json()
    path = f"{base}/{uploaded['id']}/versions/1/parse"
    overview = http.get(f"{base}/parse-issues", headers=viewer).json()
    assert (overview["parsed_documents"], overview["total_documents"]) == (0, 1)
    assert overview["issues"][0]["job_status"] == "QUEUED"
    assert http.post(f"{path}?force=true", headers=viewer).status_code == 403
    with Session(engine) as db:
        assert process_next(db)
        original = db.scalar(select(DocumentChunk))
        original_id = original.id
    assert http.get(f"{base}/parse-issues", headers=owner).json()["issues"] == []
    assert http.post(path, headers=owner).json()["status"] == "SUCCEEDED"
    forced = http.post(f"{path}?force=true", headers=owner)
    assert forced.status_code == 202
    assert forced.json()["status"] == "QUEUED"
    assert forced.json()["attempts"] == 0
    assert (
        http.get(f"{base}/parse-issues", headers=owner).json()["issues"][0]["status"] == "UPLOADED"
    )
    assert (
        http.get(f"/api/v1/knowledge-bases/{kb_id}/search/chunks?q=Alpha", headers=owner).json()
        == []
    )
    with Session(engine) as db:
        assert process_next(db)
        assert db.scalar(select(DocumentChunk)).id != original_id
        assert db.get(VectorDeletionJob, original_id) is not None
    deleted = []
    monkeypatch.setattr(
        "knowledge_api.document_lifecycle.delete_vectors",
        lambda _settings, ids: deleted.extend(ids),
    )
    with Session(engine) as db:
        assert reconcile_next_projection(db)
        assert db.get(VectorDeletionJob, original_id) is None
    assert deleted == [original_id]
    assert http.get(f"{base}/parse-issues", headers=owner).json()["parsed_documents"] == 1
    assert http.get(f"/api/v1/knowledge-bases/{kb_id}/search/chunks?q=Alpha", headers=owner).json()


def test_force_reparse_after_retry_limit_and_unsupported_issue(client, tmp_path, monkeypatch):
    http, engine = client
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    add_user(engine, "owner")
    owner = login(http, "owner")
    kb_id = http.post("/api/v1/knowledge-bases", headers=owner, json={"name": "Gaps"}).json()["id"]
    base = f"/api/v1/knowledge-bases/{kb_id}/documents"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    content = BytesIO()
    writer.write(content)
    failed = http.post(base, headers=owner, files={"file": ("scan.pdf", content.getvalue())}).json()
    legacy = http.post(
        base,
        headers=owner,
        files={"file": ("legacy.ppt", bytes.fromhex("d0cf11e0a1b11ae1") + b"ppt")},
    ).json()
    path = f"{base}/{failed['id']}/versions/1/parse"
    with Session(engine) as db:
        assert process_next(db)
    for _ in range(2):
        assert http.post(path, headers=owner).status_code == 202
        with Session(engine) as db:
            assert process_next(db)
    assert http.post(path, headers=owner).status_code == 409
    issues = http.get(f"{base}/parse-issues", headers=owner).json()["issues"]
    by_id = {item["document_id"]: item for item in issues}
    assert by_id[failed["id"]]["attempts"] == 3
    assert by_id[failed["id"]]["parse_error"]
    assert by_id[legacy["id"]]["supported"] is False
    queued = http.post(f"{path}?force=true", headers=owner)
    assert queued.status_code == 202
    assert queued.json()["attempts"] == 0
    with Session(engine) as db:
        assert (
            db.scalar(
                select(DocumentParseJob).where(
                    DocumentParseJob.document_version_id == queued.json()["document_version_id"]
                )
            ).status
            == "QUEUED"
        )
