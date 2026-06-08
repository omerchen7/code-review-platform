from __future__ import annotations

from app.cache import compute_cache_key, lookup_cached_scan
from tests.conftest import make_scan

# Cache key components that match make_scan's defaults.
_BASE = dict(
    content_hash="abc",
    ruleset_hash="def",
    llm_provider="ollama",
    llm_model="qwen2.5-coder:7b",
    llm_temperature=0.0,
)


# ---------------------------------------------------------------------------
# compute_cache_key
# ---------------------------------------------------------------------------

def test_cache_key_is_64_char_hex():
    key = compute_cache_key(**_BASE)
    assert len(key) == 64
    assert all(c in "0123456789abcdef" for c in key)


def test_cache_key_is_stable():
    assert compute_cache_key(**_BASE) == compute_cache_key(**_BASE)


def test_cache_key_changes_on_content_hash():
    assert compute_cache_key(**_BASE) != compute_cache_key(**{**_BASE, "content_hash": "other"})


def test_cache_key_changes_on_ruleset_hash():
    assert compute_cache_key(**_BASE) != compute_cache_key(**{**_BASE, "ruleset_hash": "other"})


def test_cache_key_changes_on_llm_provider():
    assert compute_cache_key(**_BASE) != compute_cache_key(**{**_BASE, "llm_provider": "lmstudio"})


def test_cache_key_changes_on_llm_model():
    assert compute_cache_key(**_BASE) != compute_cache_key(**{**_BASE, "llm_model": "llama3.2:3b"})


def test_cache_key_changes_on_llm_temperature():
    assert compute_cache_key(**_BASE) != compute_cache_key(**{**_BASE, "llm_temperature": 0.7})


# ---------------------------------------------------------------------------
# lookup_cached_scan
# ---------------------------------------------------------------------------

def test_lookup_returns_completed_non_expired_scan(db_session):
    scan = make_scan("scan-1", "completed", expires_delta_hours=1)
    db_session.add(scan)
    db_session.commit()

    result = lookup_cached_scan(db_session, **_BASE)
    assert result is not None
    assert result.id == "scan-1"


def test_lookup_ignores_expired_scan(db_session):
    scan = make_scan("scan-2", "completed", expires_delta_hours=-1)
    db_session.add(scan)
    db_session.commit()

    assert lookup_cached_scan(db_session, **_BASE) is None


def test_lookup_ignores_failed_scan(db_session):
    scan = make_scan("scan-3", "failed", expires_delta_hours=1)
    db_session.add(scan)
    db_session.commit()

    assert lookup_cached_scan(db_session, **_BASE) is None


def test_lookup_ignores_pending_scan(db_session):
    scan = make_scan("scan-4", "pending", expires_delta_hours=1)
    db_session.add(scan)
    db_session.commit()

    assert lookup_cached_scan(db_session, **_BASE) is None


def test_lookup_ignores_running_scan(db_session):
    scan = make_scan("scan-5", "running", expires_delta_hours=1)
    db_session.add(scan)
    db_session.commit()

    assert lookup_cached_scan(db_session, **_BASE) is None


def test_lookup_returns_none_when_no_match(db_session):
    scan = make_scan("scan-6", "completed", expires_delta_hours=1, content_hash="other")
    db_session.add(scan)
    db_session.commit()

    assert lookup_cached_scan(db_session, **_BASE) is None


def test_lookup_returns_most_recent_when_multiple_match(db_session):
    from datetime import timedelta, timezone
    from datetime import datetime

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    older = make_scan("scan-old", "completed", expires_delta_hours=1)
    older.created_at = now - timedelta(hours=2)

    newer = make_scan("scan-new", "completed", expires_delta_hours=1)
    newer.created_at = now - timedelta(hours=1)

    db_session.add_all([older, newer])
    db_session.commit()

    result = lookup_cached_scan(db_session, **_BASE)
    assert result is not None
    assert result.id == "scan-new"
