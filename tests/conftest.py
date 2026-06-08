from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Scan

# ---------------------------------------------------------------------------
# Shared DB fixture: fresh in-memory SQLite for every test that needs it
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session():
    """Yield an isolated in-memory SQLite session and tear it down afterwards."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


# ---------------------------------------------------------------------------
# Helper: build a Scan row with sensible defaults
# ---------------------------------------------------------------------------

def make_scan(
    scan_id: str,
    status: str,
    *,
    expires_delta_hours: float = 24,
    content_hash: str = "abc",
    ruleset_hash: str = "def",
    llm_provider: str = "ollama",
    llm_model: str = "qwen2.5-coder:7b",
    llm_temperature: float = 0.0,
) -> Scan:
    """Return an unsaved Scan ORM instance for use in tests."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return Scan(
        id=scan_id,
        file_name="test.py",
        content_hash=content_hash,
        ruleset_hash=ruleset_hash,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_temperature=llm_temperature,
        status=status,
        error=None,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=expires_delta_hours),
    )
