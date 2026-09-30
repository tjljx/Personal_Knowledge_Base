from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str


class ChangePasswordInput(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=12, max_length=128)


class TokenOutput(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    role: str
    status: str


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=128)


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class KnowledgeBaseOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    owner_id: str
    status: str
    cloud_enabled: bool
    created_at: datetime


class CloudAccessUpdate(BaseModel):
    enabled: bool


class MemberCreate(BaseModel):
    user_id: str
    member_role: str = Field(default="EDITOR", pattern=r"^(VIEWER|EDITOR)$")


class DocumentOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    knowledge_base_id: str
    title: str
    description: str
    source: str
    file_type: str
    created_by: str
    tags: list[str]
    current_version: int
    status: str
    created_at: datetime
    updated_at: datetime


class DocumentMetadataUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    source: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=20)


class DocumentBatchError(BaseModel):
    filename: str
    detail: str


class DocumentBatchOutput(BaseModel):
    items: list[DocumentOutput]
    errors: list[DocumentBatchError]


class DocumentVersionOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    version_no: int
    original_filename: str
    content_type: str
    sha256: str
    size_bytes: int
    status: str
    parse_error: str | None
    job_status: str | None
    created_at: datetime


class DocumentParseIssueOutput(BaseModel):
    document_id: str
    title: str
    version_no: int
    original_filename: str
    status: str
    job_status: str | None
    attempts: int
    parse_error: str | None
    supported: bool


class DocumentParseOverviewOutput(BaseModel):
    total_documents: int
    parsed_documents: int
    issues: list[DocumentParseIssueOutput]


class DocumentParseJobOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_version_id: str
    status: str
    attempts: int


class DocumentParseResultOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_version_id: str
    parser_name: str
    parser_version: str
    markdown: str
    structure: dict
    created_at: datetime


class ProvenanceChunk(BaseModel):
    id: str
    ordinal: int
    page_no: int | None
    block_type: str
    excerpt: str


class ProvenanceVersion(BaseModel):
    version_no: int
    status: str
    parser_name: str | None
    page_count: int
    chunk_count: int
    chunks: list[ProvenanceChunk]


class ProvenanceOutput(BaseModel):
    document_id: str
    title: str
    current_version: int
    versions: list[ProvenanceVersion]
    more_versions: bool


class SearchLocation(BaseModel):
    page: int | None = None
    block_type: str | None = None


class DocumentSearchResult(BaseModel):
    document: DocumentOutput
    matched_in: list[str]
    snippet: str
    locations: list[SearchLocation]


class ChunkSearchResult(BaseModel):
    document: DocumentOutput
    version_no: int
    chunk_id: str
    page_no: int | None
    block_type: str
    content: str


class AskInput(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


class CitationOutput(BaseModel):
    number: int
    document_id: str
    document_title: str
    version_no: int
    page_no: int | None
    chunk_id: str
    quote: str
    graph_evidence: list[dict[str, str]] = Field(default_factory=list)


class CitationSourceOutput(CitationOutput):
    content: str
    file_type: str
    original_filename: str


class AskOutput(BaseModel):
    answer: str
    citations: list[CitationOutput]


class ChatSessionCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=200)


class ChatSessionOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    knowledge_base_id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessageInput(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


class ChatMessageOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    role: str
    content: str
    citations: list[dict]
    model: str | None
    created_at: datetime
