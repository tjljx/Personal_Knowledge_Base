from pathlib import Path
from uuid import uuid4

from conftest import add_user, login
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api.config import get_settings
from knowledge_api.models import DocumentVersion


def test_document_versions_storage_and_permissions(client, tmp_path, monkeypatch):
    http, engine = client
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    add_user(engine, "owner")
    viewer_id = add_user(engine, "viewer")
    outsider_id = add_user(engine, "outsider")
    owner = login(http, "owner")
    viewer = login(http, "viewer")
    outsider = login(http, "outsider")
    kb_id = http.post("/api/v1/knowledge-bases", headers=owner, json={"name": "KB"}).json()["id"]
    base = f"/api/v1/knowledge-bases/{kb_id}/documents"

    assert http.post(base, headers=outsider, files={"file": ("a.md", b"hello")}).status_code == 404
    http.post(
        f"/api/v1/knowledge-bases/{kb_id}/members",
        headers=owner,
        json={"user_id": viewer_id, "member_role": "VIEWER"},
    )
    assert http.post(base, headers=viewer, files={"file": ("a.md", b"hello")}).status_code == 403

    first = http.post(base, headers=owner, files={"file": ("a.md", b"hello")})
    assert first.status_code == 201
    doc_id = first.json()["id"]
    second = http.post(base, headers=owner, files={"file": ("copy.md", b"hello")})
    assert second.status_code == 201
    assert len(list((tmp_path / "blobs").rglob("*"))) == 2  # one directory and one blob

    path = f"{base}/{doc_id}"
    assert http.get(path, headers=outsider).status_code == 404
    assert http.get(path, headers=viewer).status_code == 200
    assert http.get(f"{path}/versions", headers=viewer).json()[0]["version_no"] == 1
    assert (
        http.post(f"{path}/versions", headers=owner, files={"file": ("a.md", b"hello")}).status_code
        == 409
    )
    updated = http.post(f"{path}/versions", headers=owner, files={"file": ("a.md", b"changed")})
    assert updated.status_code == 201
    assert updated.json()["version_no"] == 2
    assert http.get(path, headers=owner).json()["current_version"] == 2
    assert http.get(f"{path}/versions/1/download", headers=viewer).content == b"hello"
    assert http.get(f"{path}/versions/2/download", headers=viewer).content == b"changed"
    assert http.get(f"{path}/versions/1/download", headers=outsider).status_code == 404
    assert http.get(f"{base}/{uuid4()}/versions", headers=viewer).status_code == 404
    with Session(engine) as db:
        assert len(db.scalars(select(DocumentVersion)).all()) == 3
    assert outsider_id


def test_upload_validation_and_editor(client, tmp_path, monkeypatch):
    http, engine = client
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 5)
    add_user(engine, "owner")
    editor_id = add_user(engine, "editor")
    owner = login(http, "owner")
    editor = login(http, "editor")
    kb_id = http.post("/api/v1/knowledge-bases", headers=owner, json={"name": "KB"}).json()["id"]
    http.post(
        f"/api/v1/knowledge-bases/{kb_id}/members",
        headers=owner,
        json={"user_id": editor_id, "member_role": "EDITOR"},
    )
    base = f"/api/v1/knowledge-bases/{kb_id}/documents"
    assert http.post(base, headers=editor, files={"file": ("bad.exe", b"123")}).status_code == 422
    assert (
        http.post(base, headers=editor, files={"file": ("fake.pdf", b"hello")}).status_code == 422
    )
    assert http.post(base, headers=editor, files={"file": ("a.md", b"123456")}).status_code == 413
    assert http.post(base, headers=editor, files={"file": ("a.md", b"")}).status_code == 422
    assert http.get(base, headers=editor).json() == []
    assert not list(Path(tmp_path / "staging").iterdir())
    assert http.post(base, headers=editor, files={"file": ("a.md", b"valid")}).status_code == 201


def test_batch_metadata_filters_and_preview(client, tmp_path, monkeypatch):
    http, engine = client
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    add_user(engine, "owner")
    add_user(engine, "other")
    owner = login(http, "owner")
    other = login(http, "other")
    kb_id = http.post("/api/v1/knowledge-bases", headers=owner, json={"name": "KB"}).json()["id"]
    base = f"/api/v1/knowledge-bases/{kb_id}/documents"
    files = [
        ("files", ("alpha.md", b"# Alpha")),
        ("files", ("bad.pdf", b"not a pdf")),
        ("files", ("page.html", b"<script>alert(1)</script><h1>Page</h1>")),
    ]
    assert http.post(f"{base}/batch", headers=other, files=files).status_code == 404
    batch = http.post(f"{base}/batch", headers=owner, files=files)
    assert batch.status_code == 200
    assert len(batch.json()["items"]) == 2
    assert batch.json()["errors"] == [{"filename": "bad.pdf", "detail": "文件内容与扩展名不匹配"}]
    doc = batch.json()["items"][0]
    path = f"{base}/{doc['id']}"
    metadata = {
        "title": "Research note",
        "description": "First",
        "source": "manual",
        "tags": ["project", "draft"],
    }
    assert http.put(path, headers=other, json=metadata).status_code == 404
    updated = http.put(path, headers=owner, json=metadata)
    assert updated.status_code == 200
    assert updated.json()["tags"] == ["draft", "project"]
    assert (
        http.put(path, headers=owner, json={**metadata, "tags": ["project", "project"]}).status_code
        == 422
    )
    filtered = http.get(
        base,
        headers=owner,
        params={
            "q": "Research",
            "tag": "project",
            "source": "manual",
            "file_type": "md",
            "page_size": 1,
        },
    )
    assert filtered.status_code == 200
    assert filtered.headers["X-Total-Count"] == "1"
    assert [item["id"] for item in filtered.json()] == [doc["id"]]
    assert http.get(base, headers=owner, params={"q": "alpha.md"}).json()[0]["id"] == doc["id"]
    assert http.get(base, headers=owner, params={"page": 2, "page_size": 1}).status_code == 200
    assert http.get(f"{path}/versions/1/preview", headers=owner).json()["content"] == "# Alpha"
    assert http.get(f"{path}/versions/1/preview", headers=other).status_code == 404
    html_id = batch.json()["items"][1]["id"]
    html_preview = http.get(f"{base}/{html_id}/versions/1/preview", headers=owner)
    assert "<script>" in html_preview.json()["content"]  # client displays text, not HTML
    too_many = [("files", (f"{i}.md", b"ok")) for i in range(11)]
    assert http.post(f"{base}/batch", headers=owner, files=too_many).status_code == 422
