from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str
    jwt_secret: str = Field(min_length=32)
    cors_origins: str = "http://localhost:5173"
    jwt_minutes: int = Field(default=480, gt=0, le=1440)
    session_idle_minutes: int = Field(default=10, gt=0, le=60)
    storage_root: Path = ROOT / "data" / "files"
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    model_provider: str = Field(default="bailian", min_length=1, max_length=40)
    model_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("MODEL_API_KEY", "BAILIAN_API_KEY")
    )
    model_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias=AliasChoices("MODEL_BASE_URL", "BAILIAN_BASE_URL"),
    )
    model_name: str = Field(
        default="qwen-plus", validation_alias=AliasChoices("MODEL_NAME", "BAILIAN_MODEL")
    )
    model_timeout_seconds: int = Field(
        default=45,
        ge=5,
        le=120,
        validation_alias=AliasChoices("MODEL_TIMEOUT_SECONDS", "BAILIAN_TIMEOUT_SECONDS"),
    )
    cloud_monthly_budget_cny: Decimal = Field(default=Decimal(200), gt=0, le=100000)
    model_input_cny_per_million: Decimal | None = Field(default=None, gt=0)
    model_output_cny_per_million: Decimal | None = Field(default=None, gt=0)
    model_max_output_tokens: int = Field(default=2048, ge=128, le=16384)
    embedding_model: str | None = None
    embedding_base_url: str = "http://127.0.0.1:11434"
    embedding_timeout_seconds: int = Field(default=120, ge=5, le=300)
    embedding_query_timeout_seconds: int = Field(default=20, ge=5, le=120)
    embedding_expected_dimensions: int | None = Field(default=None, gt=0, le=8192)
    embedding_min_similarity: float = Field(default=0.5, ge=0, le=1)
    embedding_query_instruction: str = ""
    vector_store_url: str | None = None
    vector_store_collection: str = "personal_knowledge_chunks_v1"
    rerank_base_url: str | None = None
    rerank_timeout_seconds: int = Field(default=20, ge=3, le=120)
    rerank_candidate_limit: int = Field(default=12, ge=2, le=20)

    @field_validator("storage_root")
    @classmethod
    def resolve_storage_root(cls, value: Path) -> Path:
        return (ROOT / value).resolve() if not value.is_absolute() else value.resolve()

    @field_validator("jwt_secret")
    @classmethod
    def reject_example_secret(cls, value: str) -> str:
        if value.startswith("replace-with-"):
            raise ValueError("必须设置独立的 JWT_SECRET，不能使用示例值")
        return value

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
