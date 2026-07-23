from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, populated from an optional .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="QA_", extra="ignore")

    base_url: str = Field(default="https://example.com", description="Starting URL for scenarios")
    headless: bool = True
    slow_mo_ms: int = Field(default=0, ge=0)
    action_timeout_ms: int = Field(default=10_000, ge=1_000)
    navigation_timeout_ms: int = Field(default=30_000, ge=1_000)
    max_retries: int = Field(default=3, ge=1, le=5)
    output_dir: Path = Path("output")
    openai_api_key: str | None = None
    llm_model: str = "gpt-4.1-mini"

    @property
    def artifact_root(self) -> Path:
        return self.output_dir / "runs"


@lru_cache
def get_settings() -> Settings:
    return Settings()

