from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


def _build_engine():
    settings = get_settings()
    connect_args = {}
    if settings.db_url.startswith("sqlite"):
        # Required for SQLite to allow the same connection to be used across
        # threads (FastAPI runs sync dependencies in a thread pool).
        connect_args["check_same_thread"] = False
    return create_engine(settings.db_url, connect_args=connect_args)


engine = _build_engine()

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session for the duration of a request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
