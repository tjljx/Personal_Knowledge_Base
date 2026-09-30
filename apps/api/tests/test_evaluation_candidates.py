from conftest import add_user, login
from sqlalchemy.orm import Session

from knowledge_api.evaluation_candidates import generate
from knowledge_api.models import (
    Document,
    DocumentChunk,
    DocumentVersion,
    GraphEntity,
    GraphEvidence,
)


def test_drafts_have_current_verbatim_source_and_are_marked_for_review(client):
    http, engine = client
    owner = add_user(engine, "eval-owner")
    headers = login(http, "eval-owner")
    kb_id = http.post(
        "/api/v1/knowledge-bases", headers=headers, json={"name": "Evaluation"}
    ).json()["id"]
    with Session(engine) as db:
        document = Document(
            knowledge_base_id=kb_id,
            title="Manual",
            file_type="md",
            status="PARSED",
            created_by=owner,
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            document_id=document.id,
            version_no=1,
            original_filename="Manual.md",
            content_type="text/markdown",
            sha256="0" * 64,
            size_bytes=100,
            status="PARSED",
            created_by=owner,
        )
        db.add(version)
        db.flush()
        chunk = DocumentChunk(
            document_version_id=version.id,
            ordinal=0,
            content="星河公司负责建设北斗平台。",
            block_type="paragraph",
            page_no=2,
        )
        db.add(chunk)
        source = GraphEntity(
            knowledge_base_id=kb_id, name="星河公司", normalized_name="星河公司", entity_type="组织"
        )
        target = GraphEntity(
            knowledge_base_id=kb_id, name="北斗平台", normalized_name="北斗平台", entity_type="产品"
        )
        db.add_all([source, target])
        db.flush()
        db.add_all(
            [
                GraphEvidence(
                    knowledge_base_id=kb_id,
                    source_entity_id=source.id,
                    target_entity_id=target.id,
                    relation_type="建设",
                    document_version_id=version.id,
                    chunk_id=chunk.id,
                    quote="星河公司负责建设北斗平台。",
                    confidence=0.9,
                    model="test",
                ),
                GraphEvidence(
                    knowledge_base_id=kb_id,
                    source_entity_id=source.id,
                    target_entity_id=target.id,
                    relation_type="运营",
                    document_version_id=version.id,
                    chunk_id=chunk.id,
                    quote="原文没有的句子",
                    confidence=0.9,
                    model="test",
                ),
            ]
        )
        db.commit()
        cases = generate(db, kb_id)
    assert len(cases) == 1
    assert cases[0]["source_quote"] == "星河公司负责建设北斗平台。"
    assert cases[0]["source_page"] == 2
    assert cases[0]["expected_chunk_ids"] == [chunk.id]
    assert cases[0]["review_status"] == "pending"
