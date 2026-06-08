from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.concurrency import ConcurrencyManager
from app.db import SessionLocal
from app.llm.base import BaseLLMProvider
from app.models import RuleResult, Scan
from app.rules import Rule

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime, consistent with DB storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def run_scan(
    scan_id: str,
    code: str,
    rules: list[Rule],
    provider: BaseLLMProvider,
    concurrency_manager: ConcurrencyManager,
) -> None:
    """Execute a scan in the background.

    This coroutine is launched via ``asyncio.create_task`` by the POST /scans
    handler and runs independently of the request lifecycle. It owns its own
    database session for its entire lifetime; it must never share or reuse the
    request-scoped session that was already closed when the handler returned.

    Session and slot lifetime:
    - ``db`` is opened at the top of the function and closed in ``finally``.
    - ``concurrency_manager.release()`` is also called in ``finally``, so the
      slot is freed regardless of success, LLM failure, or DB error.
    """
    db = SessionLocal()
    try:
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if scan is None:
            raise RuntimeError(f"Scan row '{scan_id}' not found in the database.")

        scan.status = "running"
        scan.error = None
        scan.updated_at = _utcnow()
        db.commit()

        rule_results: list[RuleResult] = []
        for rule in rules:
            verdict = await provider.evaluate_rule(code, rule)
            rule_results.append(
                RuleResult(
                    scan_id=scan_id,
                    rule_id=rule.id,
                    rule_name=rule.name,
                    adheres=verdict.adheres,
                    reason=verdict.reason,
                    created_at=_utcnow(),
                )
            )

        db.add_all(rule_results)
        scan.status = "completed"
        scan.error = None
        scan.updated_at = _utcnow()
        db.commit()

        logger.info("Scan '%s' completed (%d rules evaluated).", scan_id, len(rule_results))

    except Exception as exc:
        logger.exception("Scan '%s' failed: %s", scan_id, exc)
        try:
            db.rollback()
            scan = db.query(Scan).filter(Scan.id == scan_id).first()
            if scan is not None:
                scan.status = "failed"
                scan.error = str(exc)
                scan.updated_at = _utcnow()
                db.commit()
        except Exception:
            logger.exception(
                "Scan '%s': could not persist failed status after error.", scan_id
            )

    finally:
        db.close()
        await concurrency_manager.release()
