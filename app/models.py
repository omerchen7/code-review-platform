from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    ruleset_hash: Mapped[str] = mapped_column(String, nullable=False)
    llm_provider: Mapped[str] = mapped_column(String, nullable=False)
    llm_model: Mapped[str] = mapped_column(String, nullable=False)
    llm_temperature: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    results: Mapped[list[RuleResult]] = relationship(
        "RuleResult",
        back_populates="scan",
        cascade="all, delete-orphan",
        lazy="select",
    )

    __table_args__ = (
        Index(
            "ix_scans_cache_lookup",
            "content_hash",
            "ruleset_hash",
            "llm_provider",
            "llm_model",
            "llm_temperature",
            "status",
        ),
    )


class RuleResult(Base):
    __tablename__ = "rule_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(
        String, ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[str] = mapped_column(String, nullable=False)
    rule_name: Mapped[str] = mapped_column(String, nullable=False)
    adheres: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    scan: Mapped[Scan] = relationship("Scan", back_populates="results")
