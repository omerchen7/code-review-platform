from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Scan


def compute_cache_key(
    content_hash: str,
    ruleset_hash: str,
    llm_provider: str,
    llm_model: str,
    llm_temperature: float,
) -> str:
    """Return a SHA-256 hex digest that uniquely identifies a set of scan inputs.

    All five parameters affect the LLM output, so a change to any of them
    should produce a different result and must not reuse a cached scan.

    A canonical JSON representation is used (``sort_keys=True``, no extra
    whitespace) to avoid ambiguity that manual string concatenation can
    introduce when field values contain the separator character.
    """
    payload = {
        "content_hash": content_hash,
        "llm_model": llm_model,
        "llm_provider": llm_provider,
        "llm_temperature": llm_temperature,
        "ruleset_hash": ruleset_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lookup_cached_scan(
    db: Session,
    content_hash: str,
    ruleset_hash: str,
    llm_provider: str,
    llm_model: str,
    llm_temperature: float,
) -> Scan | None:
    """Return a reusable completed scan, or None if no valid cache entry exists.

    A scan is considered reusable when ALL of the following hold:
    - ``content_hash``, ``ruleset_hash``, ``llm_provider``, ``llm_model``,
      and ``llm_temperature`` all match exactly.
    - ``status`` is ``"completed"`` (pending, running, and failed scans are
      never reused).
    - ``expires_at`` is in the future (expired results are not returned even
      if they would otherwise match; this prevents stale verdicts from being
      surfaced as valid cache hits).

    When multiple matching rows exist (e.g. the same file was scanned twice
    before any result expired) the most recently created one is returned.

    The query uses the composite index on
    ``(content_hash, ruleset_hash, llm_provider, llm_model, llm_temperature,
    status)`` defined on the ``scans`` table, so no full-table scan is needed.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    return (
        db.query(Scan)
        .filter(
            Scan.content_hash == content_hash,
            Scan.ruleset_hash == ruleset_hash,
            Scan.llm_provider == llm_provider,
            Scan.llm_model == llm_model,
            Scan.llm_temperature == llm_temperature,
            Scan.status == "completed",
            Scan.expires_at > now,
        )
        .order_by(Scan.created_at.desc())
        .first()
    )
