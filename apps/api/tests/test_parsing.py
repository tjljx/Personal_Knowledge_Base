import zipfile
from decimal import Decimal
from io import BytesIO

from conftest import add_user, login
from pydantic import SecretStr
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api import parsers
from knowledge_api.config import get_settings
from knowledge_api.model_gateway import ModelAnswer
from knowledge_api.models import DocumentChunk, DocumentParseJob, DocumentParseResult, KnowledgeBase
from knowledge_api.parsers import parse_file
from knowledge_api.word_page_backfill import locate_pages
from knowledge_api.worker import process_next


def office_file(path, entries: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        for name, content in entries.items():
            archive.writestr(name, content)


def test_markdown_parse_job_permissions_and_result(client, tmp_path, monkeypatch):
    http, engine = client
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    add_user(engine, "owner")
    viewer_id = add_user(engine, "viewer")
    add_user(engine, "other")
    owner = login(http, "owner")
    viewer = login(http, "viewer")
    other = login(http, "other")
    kb_id = http.post("/api/v1/knowledge-bases", headers=owner, json={"name": "KB"}).json()["id"]
    http.post(
        f"/api/v1/knowledge-bases/{kb_id}/members",
        headers=owner,
        json={"user_id": viewer_id, "member_role": "VIEWER"},
    )
    base = f"/api/v1/knowledge-bases/{kb_id}/documents"
    upload = http.post(base, headers=owner, files={"file": ("guide.md", b"# Guide\n\nHello world")})
    assert upload.status_code == 201
    path = f"{base}/{upload.json()['id']}/versions/1"
    versions = http.get(f"{base}/{upload.json()['id']}/versions", headers=owner).json()
    assert versions[0]["job_status"] == "QUEUED"
    assert http.post(f"{path}/parse", headers=viewer).status_code == 403
    assert http.get(f"{path}/parsed", headers=other).status_code == 404
    assert http.get(f"{path}/parsed", headers=viewer).status_code == 404

    with Session(engine) as db:
        assert process_next(db)
        assert not process_next(db)
        assert len(db.scalars(select(DocumentParseResult)).all()) == 1
    result = http.get(f"{path}/parsed", headers=viewer)
    assert result.status_code == 200
    assert result.json()["parser_name"] == "markdown-basic"
    assert result.json()["structure"]["pages"][0]["blocks"][0]["type"] == "heading"
    assert "Hello world" in result.json()["markdown"]
    graph_path = f"{base}/{upload.json()['id']}/provenance"
    assert http.get(graph_path, headers=other).status_code == 404
    graph = http.get(graph_path, headers=viewer)
    assert graph.status_code == 200
    assert graph.json()["title"] == "guide.md"
    assert graph.json()["versions"][0]["chunk_count"] >= 1
    assert "Hello world" in " ".join(
        chunk["excerpt"] for chunk in graph.json()["versions"][0]["chunks"]
    )
    assert http.get(f"{base}/{upload.json()['id']}", headers=owner).json()["status"] == "PARSED"
    again = http.post(f"{path}/parse", headers=owner)
    assert again.status_code == 202
    assert again.json()["status"] == "SUCCEEDED"
    with Session(engine) as db:
        assert len(db.scalars(select(DocumentParseJob)).all()) == 1


def test_scanned_pdf_failure_and_idempotent_retry(client, tmp_path, monkeypatch):
    http, engine = client
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    add_user(engine, "owner")
    owner = login(http, "owner")
    kb_id = http.post("/api/v1/knowledge-bases", headers=owner, json={"name": "KB"}).json()["id"]
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    content = BytesIO()
    writer.write(content)
    base = f"/api/v1/knowledge-bases/{kb_id}/documents"
    uploaded = http.post(base, headers=owner, files={"file": ("scan.pdf", content.getvalue())})
    assert uploaded.status_code == 201
    path = f"{base}/{uploaded.json()['id']}/versions/1"
    for attempt in range(1, 4):
        with Session(engine) as db:
            assert process_next(db)
        version = http.get(f"{base}/{uploaded.json()['id']}/versions", headers=owner).json()[0]
        assert version["status"] == "FAILED"
        assert "OCR" in version["parse_error"]
        if attempt < 3:
            retry = http.post(f"{path}/parse", headers=owner)
            assert retry.status_code == 202
            assert retry.json()["attempts"] == attempt
    assert http.post(f"{path}/parse", headers=owner).status_code == 409
    assert http.get(f"{path}/parsed", headers=owner).status_code == 404
    with Session(engine) as db:
        assert len(db.scalars(select(DocumentParseJob)).all()) == 1
        assert not db.scalars(select(DocumentParseResult)).all()


def test_scanned_pdf_and_image_ocr_keep_source_page(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with pdf.open("wb") as output:
        writer.write(output)
    monkeypatch.setattr(parsers, "_ocr_pdf_page", lambda _path, page: f"第 {page} 页扫描资料")
    parsed = parse_file(pdf, pdf.name)
    assert parsed.parser_name == "pypdf+tesseract"
    assert parsed.structure["parser_config"]["ocr_pages"] == 1
    assert parsed.structure["pages"][0]["blocks"][0]["text"] == "第 1 页扫描资料"

    image = tmp_path / "photo.png"
    image.write_bytes(b"image bytes handled by OCR executable")
    monkeypatch.setattr(parsers, "_run_ocr", lambda _path: "图像里的文字")
    parsed_image = parse_file(image, image.name)
    assert parsed_image.parser_name == "tesseract-image"
    assert parsed_image.structure["pages"][0]["number"] == 1


def test_html_parser_ignores_script_and_preserves_headings(tmp_path):
    path = tmp_path / "page.html"
    path.write_text("<h1>Project</h1><script>secret()</script><p>Useful text</p>", encoding="utf-8")
    result = parse_file(path, "page.html")
    assert result.parser_name == "html-basic"
    assert result.markdown.startswith("# Project")
    assert "Useful text" in result.markdown
    assert "secret" not in result.markdown


def test_word_parser_uses_rendered_page_boundaries(tmp_path, monkeypatch):
    path = tmp_path / "memo.docx"
    path.write_bytes(b"word content")
    pages = [
        {"number": 1, "blocks": [{"type": "paragraph", "text": "Opening section"}]},
        {"number": 2, "blocks": [{"type": "paragraph", "text": "Closing section"}]},
    ]
    monkeypatch.setattr(parsers, "_render_word_pages", lambda *_: pages)
    result = parse_file(path, path.name)
    assert result.parser_name == "libreoffice-word-pdf"
    assert result.structure["parser_config"]["pagination"] == "rendered_pdf"
    assert [page["number"] for page in result.structure["pages"]] == [1, 2]
    assert "Closing section" in result.markdown


def test_word_page_backfill_keeps_uncertain_matches_unpaged():
    from types import SimpleNamespace

    pages = [
        {"number": 1, "blocks": [{"text": "Opening section has unique text."}]},
        {"number": 2, "blocks": [{"text": "Closing section has unique text. Repeated heading."}]},
        {"number": 3, "blocks": [{"text": "Repeated heading. Final appendix."}]},
    ]
    chunks = [
        SimpleNamespace(id="first", content="Opening section has unique text."),
        SimpleNamespace(id="second", content="Closing section has unique text."),
        SimpleNamespace(id="ambiguous", content="Repeated heading."),
    ]
    assert locate_pages(chunks, pages) == {"first": 1, "second": 2, "ambiguous": None}


def test_office_parsers_extract_docx_pptx_and_xlsx_text(tmp_path):
    docx = tmp_path / "memo.docx"
    office_file(
        docx, {"word/document.xml": "<document><p><r><t>DOCX content</t></r></p></document>"}
    )
    pptx = tmp_path / "deck.pptx"
    office_file(pptx, {"ppt/slides/slide1.xml": "<slide><t>Slide content</t></slide>"})
    xlsx = tmp_path / "sheet.xlsx"
    office_file(
        xlsx,
        {
            "xl/sharedStrings.xml": "<sst><si><t>Revenue</t></si></sst>",
            "xl/worksheets/sheet1.xml": "<worksheet><sheetData><row><c t='s'><v>0</v></c><c><v>42</v></c></row></sheetData></worksheet>",
        },
    )
    assert parse_file(docx, docx.name).markdown == "DOCX content"
    assert parse_file(pptx, pptx.name).structure["pages"][0]["blocks"][0]["text"] == "Slide content"
    assert parse_file(xlsx, xlsx.name).markdown == "Revenue | 42"


def test_old_version_job_does_not_change_new_version_status(client, tmp_path, monkeypatch):
    http, engine = client
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    add_user(engine, "owner")
    owner = login(http, "owner")
    kb_id = http.post("/api/v1/knowledge-bases", headers=owner, json={"name": "KB"}).json()["id"]
    base = f"/api/v1/knowledge-bases/{kb_id}/documents"
    first = http.post(base, headers=owner, files={"file": ("note.md", b"# One")}).json()
    path = f"{base}/{first['id']}"
    assert (
        http.post(
            f"{path}/versions", headers=owner, files={"file": ("note.md", b"# Two")}
        ).status_code
        == 201
    )
    with Session(engine) as db:
        assert process_next(db)
    assert http.get(path, headers=owner).json()["status"] == "UPLOADED"
    with Session(engine) as db:
        assert process_next(db)
    assert http.get(path, headers=owner).json()["status"] == "PARSED"
    graph = http.get(f"{path}/provenance", headers=owner).json()
    assert [version["version_no"] for version in graph["versions"]] == [2, 1]
    assert all(version["chunk_count"] >= 1 for version in graph["versions"])


def test_searches_authorized_current_parsed_content(client, tmp_path, monkeypatch):
    http, engine = client
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    monkeypatch.setattr(get_settings(), "model_api_key", SecretStr("test-key"))
    monkeypatch.setattr(get_settings(), "model_input_cny_per_million", Decimal(100))
    monkeypatch.setattr(get_settings(), "model_output_cny_per_million", Decimal(100))
    add_user(engine, "owner")
    viewer_id = add_user(engine, "viewer")
    add_user(engine, "outsider")
    owner = login(http, "owner")
    viewer = login(http, "viewer")
    outsider = login(http, "outsider")
    kb_id = http.post("/api/v1/knowledge-bases", headers=owner, json={"name": "KB"}).json()["id"]
    with Session(engine) as db:
        db.get(KnowledgeBase, kb_id).cloud_enabled = True
        db.commit()
    http.post(
        f"/api/v1/knowledge-bases/{kb_id}/members",
        headers=owner,
        json={"user_id": viewer_id, "member_role": "VIEWER"},
    )
    base = f"/api/v1/knowledge-bases/{kb_id}/documents"
    document = http.post(
        base, headers=owner, files={"file": ("plan.md", b"# Roadmap\n\nSecret launch phrase")}
    ).json()
    with Session(engine) as db:
        assert process_next(db)
    endpoint = f"/api/v1/knowledge-bases/{kb_id}/search"
    assert http.get(endpoint, headers=outsider, params={"q": "launch"}).status_code == 404
    response = http.get(endpoint, headers=viewer, params={"q": "launch"})
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "1"
    item = response.json()[0]
    assert item["document"]["id"] == document["id"]
    assert item["matched_in"] == ["正文"]
    assert "Secret launch phrase" in item["snippet"]
    assert item["locations"] == [{"page": None, "block_type": "paragraph"}]
    chunk_endpoint = f"{endpoint}/chunks"
    assert http.get(chunk_endpoint, headers=outsider, params={"q": "launch"}).status_code == 404
    chunk_response = http.get(chunk_endpoint, headers=viewer, params={"q": "launch"})
    assert chunk_response.headers["X-Total-Count"] == "1"
    chunk = chunk_response.json()[0]
    assert chunk["document"]["id"] == document["id"]
    assert chunk["version_no"] == 1
    assert chunk["page_no"] is None
    assert chunk["block_type"] == "paragraph"
    assert "Secret launch phrase" in chunk["content"]
    monkeypatch.setattr(
        "knowledge_api.main.answer_with_sources",
        lambda *_, **__: ModelAnswer("Grounded answer [1]", 20, 10),
    )
    answer = http.post(
        f"/api/v1/knowledge-bases/{kb_id}/ask",
        headers=viewer,
        json={"question": "launch phrase"},
    )
    assert answer.status_code == 200
    assert answer.json()["answer"] == "Grounded answer [1]"
    assert answer.json()["citations"][0]["document_id"] == document["id"]
    assert answer.json()["citations"][0]["page_no"] is None
    source_endpoint = (
        f"/api/v1/knowledge-bases/{kb_id}/citations/{answer.json()['citations'][0]['chunk_id']}"
    )
    assert http.get(source_endpoint, headers=outsider).status_code == 404
    source = http.get(source_endpoint, headers=viewer)
    assert source.status_code == 200
    assert source.json()["page_no"] is None
    assert source.json()["original_filename"] == "plan.md"
    assert "Secret launch phrase" in source.json()["content"]
    fallback_answer = http.post(
        f"/api/v1/knowledge-bases/{kb_id}/ask",
        headers=viewer,
        json={"question": "unrelated topic"},
    )
    assert fallback_answer.status_code == 200
    assert fallback_answer.json()["citations"] == []
    assert "无法可靠回答" in fallback_answer.json()["answer"]
    monkeypatch.setattr(
        "knowledge_api.main.answer_with_sources",
        lambda *_, **__: ModelAnswer("Only the body is relevant [2]", 20, 10),
    )
    selected_answer = http.post(
        f"/api/v1/knowledge-bases/{kb_id}/ask",
        headers=viewer,
        json={"question": "Roadmap launch"},
    )
    assert [citation["number"] for citation in selected_answer.json()["citations"]] == [2]
    monkeypatch.setattr(
        "knowledge_api.main.answer_with_sources",
        lambda *_, **__: ModelAnswer("Unsupported claim [9]", 20, 10),
    )
    unsupported = http.post(
        f"/api/v1/knowledge-bases/{kb_id}/ask",
        headers=viewer,
        json={"question": "Roadmap launch"},
    )
    assert unsupported.json()["citations"] == []
    assert "无法可靠回答" in unsupported.json()["answer"]
    captured = {}

    def capture_history(_settings, _question, sources, history=None):
        captured["history"] = history
        captured["sources"] = sources
        return ModelAnswer("Grounded answer [1]", 20, 10)

    monkeypatch.setattr("knowledge_api.main.answer_with_sources", capture_history)
    sessions_endpoint = f"/api/v1/knowledge-bases/{kb_id}/chat-sessions"
    session = http.post(sessions_endpoint, headers=viewer, json={"title": "新对话"})
    assert session.status_code == 201
    session_id = session.json()["id"]
    message = http.post(
        f"{sessions_endpoint}/{session_id}/messages",
        headers=viewer,
        json={"question": "launch phrase"},
    )
    assert message.status_code == 200
    assert message.json()["role"] == "assistant"
    follow_up = http.post(
        f"{sessions_endpoint}/{session_id}/messages",
        headers=viewer,
        json={"question": "tell me more about it"},
    )
    assert follow_up.status_code == 200
    assert [item["role"] for item in captured["history"]] == ["user", "assistant"]
    assert captured["history"][0]["content"] == "launch phrase"
    assert "Secret launch phrase" in captured["sources"][0].content
    history = http.get(f"{sessions_endpoint}/{session_id}/messages", headers=viewer).json()
    assert [item["role"] for item in history] == ["user", "assistant", "user", "assistant"]
    assert http.get(sessions_endpoint, headers=viewer).json()[0]["title"] == "launch phrase"
    assert http.get(f"{sessions_endpoint}/{session_id}/messages", headers=owner).status_code == 404
    assert (
        http.post(
            f"/api/v1/knowledge-bases/{kb_id}/ask",
            headers=outsider,
            json={"question": "launch phrase"},
        ).status_code
        == 404
    )
    assert (
        http.put(
            f"{base}/{document['id']}",
            headers=owner,
            json={"title": "Roadmap", "source": "internal", "tags": ["release"], "description": ""},
        ).status_code
        == 200
    )
    filtered = http.get(
        endpoint,
        headers=owner,
        params={"q": "launch", "tag": "release", "source": "internal", "file_type": "md"},
    )
    assert filtered.headers["X-Total-Count"] == "1"
    assert http.get(endpoint, headers=owner, params={"q": "launch", "tag": "other"}).json() == []
    assert (
        "文件名"
        in http.get(endpoint, headers=owner, params={"q": "plan.md"}).json()[0]["matched_in"]
    )

    assert (
        http.post(
            f"{base}/{document['id']}/versions",
            headers=owner,
            files={"file": ("plan.md", b"# Roadmap\n\nReplacement body")},
        ).status_code
        == 201
    )
    with Session(engine) as db:
        assert process_next(db)
        assert len(db.scalars(select(DocumentChunk)).all()) == 4
    assert http.get(endpoint, headers=owner, params={"q": "Secret launch"}).json() == []
    assert http.get(chunk_endpoint, headers=owner, params={"q": "Secret launch"}).json() == []
    assert (
        http.get(endpoint, headers=owner, params={"q": "Replacement"}).json()[0]["document"]["id"]
        == document["id"]
    )
