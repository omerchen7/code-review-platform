from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.concurrency import ConcurrencyManager
from app.db import Base
from app.models import RuleResult, Scan
from app.rules import Rule
from app.scanner import run_scan
from tests.conftest import FailingLLMProvider, FakeLLMProvider

# A minimal Rule that does not touch the real rules.yaml.
_RULE = Rule(
    id="test_rule",
    name="Test Rule",
    version="1.0",
    description="A minimal rule for scanner tests.",
    prompt_template="check {code}",
    enabled=True,
    order=1,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _pending_scan(scan_id: str) -> Scan:
    now = _utcnow()
    return Scan(
        id=scan_id,
        file_name="test.py",
        content_hash="abc",
        ruleset_hash="def",
        llm_provider="ollama",
        llm_model="qwen2.5-coder:7b",
        llm_temperature=0.0,
        status="pending",
        error=None,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
    )


# ---------------------------------------------------------------------------
# Fixture: in-memory SQLite with app.scanner.SessionLocal patched
# ---------------------------------------------------------------------------

@pytest.fixture
def scanner_db(monkeypatch):
    """Patch scanner.SessionLocal to use an in-memory SQLite engine.

    Yields the sessionmaker factory so tests can open their own sessions
    for setup and verification (separate from the session run_scan opens).

    StaticPool ensures all connections — the setup session, the session opened
    inside run_scan, and the verification session — share the same underlying
    DBAPI connection and therefore the same in-memory SQLite database.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr("app.scanner.SessionLocal", TestSession)
    yield TestSession
    Base.metadata.drop_all(engine)
    engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed(TestSession, scan: Scan) -> None:
    db = TestSession()
    db.add(scan)
    db.commit()
    db.close()


def _fetch_scan(TestSession, scan_id: str) -> Scan:
    db = TestSession()
    result = db.query(Scan).filter(Scan.id == scan_id).first()
    db.close()
    return result


def _fetch_results(TestSession, scan_id: str) -> list[RuleResult]:
    db = TestSession()
    results = db.query(RuleResult).filter(RuleResult.scan_id == scan_id).all()
    db.close()
    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_successful_scan_sets_completed_status(scanner_db):
    _seed(scanner_db, _pending_scan("s1"))

    mgr = ConcurrencyManager(1)
    await mgr.try_acquire()

    await run_scan("s1", "x = 1", [_RULE], FakeLLMProvider(), mgr)

    scan = _fetch_scan(scanner_db, "s1")
    assert scan.status == "completed"
    assert scan.error is None


async def test_successful_scan_persists_one_result_per_rule(scanner_db):
    _seed(scanner_db, _pending_scan("s2"))

    mgr = ConcurrencyManager(1)
    await mgr.try_acquire()

    await run_scan("s2", "x = 1", [_RULE], FakeLLMProvider(), mgr)

    results = _fetch_results(scanner_db, "s2")
    assert len(results) == 1
    assert results[0].rule_id == "test_rule"
    assert results[0].adheres is True
    assert results[0].reason is not None


async def test_successful_scan_releases_concurrency_slot(scanner_db):
    _seed(scanner_db, _pending_scan("s3"))

    mgr = ConcurrencyManager(1)
    await mgr.try_acquire()
    assert mgr.active_count == 1

    await run_scan("s3", "x = 1", [_RULE], FakeLLMProvider(), mgr)

    assert mgr.active_count == 0


async def test_provider_failure_sets_failed_status(scanner_db):
    _seed(scanner_db, _pending_scan("s4"))

    mgr = ConcurrencyManager(1)
    await mgr.try_acquire()

    await run_scan("s4", "x = 1", [_RULE], FailingLLMProvider(), mgr)

    scan = _fetch_scan(scanner_db, "s4")
    assert scan.status == "failed"
    assert scan.error is not None
    assert "Simulated LLM failure" in scan.error


async def test_provider_failure_releases_concurrency_slot(scanner_db):
    _seed(scanner_db, _pending_scan("s5"))

    mgr = ConcurrencyManager(1)
    await mgr.try_acquire()

    await run_scan("s5", "x = 1", [_RULE], FailingLLMProvider(), mgr)

    assert mgr.active_count == 0


async def test_missing_scan_id_does_not_raise(scanner_db):
    """run_scan must not propagate exceptions to the caller (create_task context)."""
    mgr = ConcurrencyManager(1)
    await mgr.try_acquire()

    # No DB row for "ghost-id" — run_scan should handle this gracefully.
    await run_scan("ghost-id", "x = 1", [_RULE], FakeLLMProvider(), mgr)


async def test_missing_scan_id_still_releases_slot(scanner_db):
    mgr = ConcurrencyManager(1)
    await mgr.try_acquire()

    await run_scan("ghost-id-2", "x = 1", [_RULE], FakeLLMProvider(), mgr)

    assert mgr.active_count == 0


async def test_multiple_rules_each_produce_a_result(scanner_db):
    second_rule = Rule(
        id="test_rule_b",
        name="Test Rule B",
        version="1.0",
        description="Another test rule.",
        prompt_template="also check {code}",
        enabled=True,
        order=2,
    )
    _seed(scanner_db, _pending_scan("s6"))

    mgr = ConcurrencyManager(1)
    await mgr.try_acquire()

    await run_scan("s6", "x = 1", [_RULE, second_rule], FakeLLMProvider(), mgr)

    results = _fetch_results(scanner_db, "s6")
    assert len(results) == 2
    rule_ids = {r.rule_id for r in results}
    assert rule_ids == {"test_rule", "test_rule_b"}
