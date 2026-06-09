from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import router
from app.concurrency import ConcurrencyManager
from app.db import Base, get_db
from app.models import Scan
from app.rules import load_rules
from tests.conftest import FakeLLMProvider, make_scan

# A short, valid Python snippet used as a test upload.
_VALID_PY = b"def greet(name):\n    return f'Hello, {name}'\n"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_state(monkeypatch):
    """Build a minimal FastAPI app wired with an in-memory DB and fake state.

    Yields (fresh_app, TestSession, concurrency_manager) so tests can inspect
    the DB and manipulate the concurrency gate directly when needed.
    """
    # StaticPool forces all connections (including those opened from sync route
    # handlers running in anyio thread-pool workers) to reuse the same underlying
    # DBAPI connection, so they all see the same in-memory SQLite database.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # run_scan opens SessionLocal() directly — point it at the test DB.
    monkeypatch.setattr("app.scanner.SessionLocal", TestSession)

    fresh_app = FastAPI()

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    fresh_app.dependency_overrides[get_db] = override_get_db

    rules = load_rules("rules/rules.yaml")
    concurrency_manager = ConcurrencyManager(5)

    fresh_app.state.rules = rules
    fresh_app.state.provider = FakeLLMProvider()
    fresh_app.state.concurrency_manager = concurrency_manager

    fresh_app.include_router(router)

    yield fresh_app, TestSession, concurrency_manager

    fresh_app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


async def _wait_for_scan(
    client: httpx.AsyncClient,
    scan_id: str,
    *,
    timeout: float = 2.0,
    interval: float = 0.01,
) -> dict:
    """Poll GET /scans/{scan_id} until the scan reaches a terminal status.

    Returns the response JSON when status is "completed" or "failed".
    Raises TimeoutError if the scan does not finish within *timeout* seconds.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        response = await client.get(f"/scans/{scan_id}")
        if response.status_code == 200:
            data = response.json()
            if data["status"] in ("completed", "failed"):
                return data
        await asyncio.sleep(interval)
    raise TimeoutError(f"Scan {scan_id!r} did not reach a terminal status within {timeout}s")


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

async def test_health_returns_200_ok(app_state):
    fresh_app, _, _ = app_state
    async with _client(fresh_app) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /rules
# ---------------------------------------------------------------------------

async def test_rules_returns_two_enabled_rules(app_state):
    fresh_app, _, _ = app_state
    async with _client(fresh_app) as client:
        response = await client.get("/rules")
    assert response.status_code == 200
    rules = response.json()["rules"]
    assert len(rules) == 2


async def test_rules_does_not_expose_prompt_template(app_state):
    fresh_app, _, _ = app_state
    async with _client(fresh_app) as client:
        response = await client.get("/rules")
    for rule in response.json()["rules"]:
        assert "prompt_template" not in rule


async def test_rules_contains_expected_ids(app_state):
    fresh_app, _, _ = app_state
    async with _client(fresh_app) as client:
        response = await client.get("/rules")
    ids = {r["id"] for r in response.json()["rules"]}
    assert ids == {"meaningful_names", "docstring_accuracy"}


# ---------------------------------------------------------------------------
# POST /scans — file validation rejections
# ---------------------------------------------------------------------------

async def test_post_rejects_non_py_extension(app_state):
    fresh_app, _, _ = app_state
    async with _client(fresh_app) as client:
        response = await client.post(
            "/scans", files={"file": ("script.txt", _VALID_PY, "text/plain")}
        )
    assert response.status_code == 400
    assert "Only .py files" in response.json()["detail"]


async def test_post_rejects_empty_file(app_state):
    fresh_app, _, _ = app_state
    async with _client(fresh_app) as client:
        response = await client.post(
            "/scans", files={"file": ("empty.py", b"", "text/plain")}
        )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


async def test_post_rejects_invalid_utf8(app_state):
    fresh_app, _, _ = app_state
    async with _client(fresh_app) as client:
        response = await client.post(
            "/scans", files={"file": ("bad.py", b"\xff\xfe\x00", "text/plain")}
        )
    assert response.status_code == 400
    assert "UTF-8" in response.json()["detail"]


async def test_post_rejects_invalid_python_syntax(app_state):
    fresh_app, _, _ = app_state
    async with _client(fresh_app) as client:
        response = await client.post(
            "/scans", files={"file": ("bad.py", b"def (:", "text/plain")}
        )
    assert response.status_code == 400
    assert "not valid Python" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /scans — new scan
# ---------------------------------------------------------------------------

async def test_post_valid_file_returns_202_not_cached(app_state):
    fresh_app, _, _ = app_state
    async with _client(fresh_app) as client:
        response = await client.post(
            "/scans", files={"file": ("code.py", _VALID_PY, "text/plain")}
        )
    assert response.status_code == 202
    data = response.json()
    assert data["cached"] is False
    assert data["status"] == "pending"
    assert "scan_id" in data


# ---------------------------------------------------------------------------
# GET /scans/{scan_id} — full lifecycle
# ---------------------------------------------------------------------------

async def test_get_scan_returns_completed_with_results(app_state):
    fresh_app, _, _ = app_state
    async with _client(fresh_app) as client:
        post = await client.post(
            "/scans", files={"file": ("code.py", _VALID_PY, "text/plain")}
        )
        assert post.status_code == 202
        scan_id = post.json()["scan_id"]

        data = await _wait_for_scan(client, scan_id)

    assert data["status"] == "completed"
    assert data["scan_id"] == scan_id
    assert "cached" not in data
    # Two rules from rules.yaml → two results
    assert len(data["results"]) == 2
    for result in data["results"]:
        assert "rule_id" in result
        assert "adheres" in result


# ---------------------------------------------------------------------------
# POST /scans — cache hit
# ---------------------------------------------------------------------------

async def test_resubmit_same_file_returns_cached_true(app_state):
    fresh_app, _, _ = app_state
    async with _client(fresh_app) as client:
        post1 = await client.post(
            "/scans", files={"file": ("code.py", _VALID_PY, "text/plain")}
        )
        assert post1.status_code == 202
        assert post1.json()["cached"] is False

        await _wait_for_scan(client, post1.json()["scan_id"])

        post2 = await client.post(
            "/scans", files={"file": ("code.py", _VALID_PY, "text/plain")}
        )

    assert post2.status_code == 202
    assert post2.json()["cached"] is True
    assert post2.json()["scan_id"] == post1.json()["scan_id"]


async def test_cache_hit_does_not_consume_slot(app_state):
    fresh_app, _, mgr = app_state
    async with _client(fresh_app) as client:
        post = await client.post("/scans", files={"file": ("code.py", _VALID_PY, "text/plain")})
        await _wait_for_scan(client, post.json()["scan_id"])
        slot_count_before = mgr.active_count

        await client.post("/scans", files={"file": ("code.py", _VALID_PY, "text/plain")})

    assert mgr.active_count == slot_count_before


# ---------------------------------------------------------------------------
# POST /scans — capacity rejection
# ---------------------------------------------------------------------------

async def test_post_at_capacity_returns_429(app_state):
    fresh_app, TestSession, mgr = app_state

    # Manually occupy all 5 slots.
    for _ in range(5):
        await mgr.try_acquire()

    try:
        async with _client(fresh_app) as client:
            response = await client.post(
                "/scans", files={"file": ("code.py", _VALID_PY, "text/plain")}
            )
        assert response.status_code == 429
    finally:
        for _ in range(5):
            await mgr.release()


async def test_post_at_capacity_does_not_create_db_row(app_state):
    fresh_app, TestSession, mgr = app_state

    for _ in range(5):
        await mgr.try_acquire()

    try:
        async with _client(fresh_app) as client:
            await client.post(
                "/scans", files={"file": ("code.py", _VALID_PY, "text/plain")}
            )
    finally:
        for _ in range(5):
            await mgr.release()

    db = TestSession()
    count = db.query(Scan).count()
    db.close()
    assert count == 0


# ---------------------------------------------------------------------------
# GET /scans/{scan_id} — edge cases
# ---------------------------------------------------------------------------

async def test_get_unknown_scan_returns_404(app_state):
    fresh_app, _, _ = app_state
    async with _client(fresh_app) as client:
        response = await client.get("/scans/does-not-exist")
    assert response.status_code == 404


async def test_get_expired_scan_returns_410(app_state):
    fresh_app, TestSession, _ = app_state

    # Insert a completed scan that expired in the past.
    db = TestSession()
    scan = make_scan("expired-scan-1", "completed", expires_delta_hours=-1)
    db.add(scan)
    db.commit()
    db.close()

    async with _client(fresh_app) as client:
        response = await client.get("/scans/expired-scan-1")
    assert response.status_code == 410
