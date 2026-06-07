from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # LLM provider
    # ------------------------------------------------------------------
    llm_provider: Literal["ollama"] = Field(
        default="ollama",
        description="LLM backend to use. Currently only 'ollama' is supported.",
    )
    llm_base_url: str = Field(
        default="http://localhost:11434",
        description="Base URL of the Ollama (or compatible) server.",
    )
    llm_model: str = Field(
        default="qwen2.5-coder:7b",
        description="Model name as recognised by the provider.",
    )
    llm_temperature: float = Field(
        default=0.0,
        description="Sampling temperature. 0.0 for deterministic output.",
    )
    llm_timeout: int = Field(
        default=120,
        description="Per-request timeout in seconds for LLM calls.",
    )

    # ------------------------------------------------------------------
    # Concurrency
    # ------------------------------------------------------------------
    max_parallel_scans: int = Field(
        default=5,
        description=(
            "Maximum number of scans that may run in parallel. "
            "A request arriving when this limit is reached is rejected with HTTP 429. "
            "This limit is per-process — run a single uvicorn worker."
        ),
    )

    # ------------------------------------------------------------------
    # Results TTL
    # ------------------------------------------------------------------
    result_ttl_hours: int = Field(
        default=24,
        description="How long (in hours) completed scan results are retained.",
    )

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    db_url: str = Field(
        default="sqlite:///./code_review.db",
        description="SQLAlchemy database URL. Use 'sqlite:///:memory:' for tests.",
    )

    # ------------------------------------------------------------------
    # File upload
    # ------------------------------------------------------------------
    max_file_bytes: int = Field(
        default=102_400,
        description="Maximum accepted source file size in bytes (default 100 KB).",
    )

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------
    rules_path: str = Field(
        default="rules/rules.yaml",
        description="Path to the YAML rules file, relative to the working directory.",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("llm_temperature")
    @classmethod
    def temperature_must_be_in_range(cls, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise ValueError("LLM_TEMPERATURE must be between 0.0 and 1.0")
        return value

    @field_validator("max_parallel_scans")
    @classmethod
    def max_parallel_scans_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("MAX_PARALLEL_SCANS must be >= 1")
        return value

    @field_validator("result_ttl_hours")
    @classmethod
    def result_ttl_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("RESULT_TTL_HOURS must be > 0")
        return value

    @field_validator("max_file_bytes")
    @classmethod
    def max_file_bytes_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("MAX_FILE_BYTES must be > 0")
        return value

    @field_validator("llm_timeout")
    @classmethod
    def llm_timeout_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("LLM_TIMEOUT must be > 0")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the application settings, loaded once and cached for the process lifetime."""
    return Settings()
