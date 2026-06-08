from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

from app.api.routes import router
from app.concurrency import ConcurrencyManager
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.llm.factory import build_provider
from app.models import Scan
from app.rules import get_rules

logger = logging.getLogger(__name__)

# How often the background cleanup task runs (seconds).
_CLEANUP_INTERVAL_SECONDS = 3600  # 1 hour


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mark_stale_scans_failed() -> None:
    """On startup, mark any scan left in 'pending' or 'running' as 'failed'.

    These rows indicate the process was killed mid-scan. Leaving them in an
    active state would permanently consume concurrency slots on the next run
    (since slots are in-memory and were lost with the old process) and would
    never transition to 'completed', confusing API callers.
    """
    db = SessionLocal()
    try:
        stale = (
            db.query(Scan)
            .filter(Scan.status.in_(["pending", "running"]))
            .all()
        )
        if stale:
            now = _utcnow()
            for scan in stale:
                scan.status = "failed"
                scan.error = "Scan interrupted: server was restarted while this scan was in progress."
                scan.updated_at = now
            db.commit()
            logger.warning(
                "Marked %d stale scan(s) as failed on startup.", len(stale)
            )
    finally:
        db.close()


async def _cleanup_expired_scans() -> None:
    """Periodically delete scan rows whose expires_at has passed.

    This is a best-effort sweep; correctness is never based solely on this
    task having run. The expires_at checks in the cache lookup and GET
    /scans/{id} handler are the authoritative expiry gates.

    RuleResult rows are deleted automatically via the ORM cascade
    ('all, delete-orphan') when their parent Scan is deleted.
    """
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
        db = SessionLocal()
        try:
            now = _utcnow()
            expired = (
                db.query(Scan)
                .filter(Scan.expires_at <= now)
                .all()
            )
            if expired:
                for scan in expired:
                    db.delete(scan)
                db.commit()
                logger.info("Cleanup: deleted %d expired scan(s).", len(expired))
        except Exception:
            logger.exception("Cleanup task encountered an error.")
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    settings = get_settings()

    # ------------------------------------------------------------------ startup
    logger.info("Starting up code-review-platform...")

    # Create all tables (no-op if they already exist).
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified.")

    # Recover from a mid-scan crash.
    _mark_stale_scans_failed()

    # Load rules once — lru_cache keeps them for the process lifetime.
    rules = get_rules()
    logger.info("Loaded %d active rule(s).", len(rules))

    # Build LLM provider from settings (no network call yet).
    provider = build_provider(settings)
    logger.info("LLM provider: %s / %s", settings.llm_provider, settings.llm_model)

    # Initialise the in-process concurrency gate.
    concurrency_manager = ConcurrencyManager(settings.max_parallel_scans)
    logger.info("Concurrency gate: max %d parallel scan(s).", settings.max_parallel_scans)

    # Store shared objects on app.state so route dependencies can reach them.
    app.state.settings = settings
    app.state.rules = rules
    app.state.provider = provider
    app.state.concurrency_manager = concurrency_manager

    # Start background cleanup (fire and forget; cancelled on shutdown).
    cleanup_task = asyncio.create_task(_cleanup_expired_scans())
    logger.info("Background cleanup task started (interval: %ds).", _CLEANUP_INTERVAL_SECONDS)

    logger.info("Startup complete.")

    yield  # application runs here

    # ----------------------------------------------------------------- shutdown
    logger.info("Shutting down...")
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Shutdown complete.")


app = FastAPI(
    title="Code Review Platform",
    description=(
        "Local POC for automatic LLM-based Python code review.\n\n"
        "Upload a `.py` file and the platform evaluates it against a set of "
        "predefined rules using a local Ollama model. Results are stored in "
        "SQLite for 24 hours and reused automatically when the same file, "
        "ruleset, and model configuration are submitted again.\n\n"
        "**Quick start:** `POST /scans` with a `.py` file → poll `GET /scans/{scan_id}` "
        "until `status` is `completed`."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.include_router(router)


@app.get(
    "/",
    include_in_schema=False,
    summary="Root navigation",
)
def root() -> dict:
    """Return basic API navigation links."""
    return {
        "name": "Code Review Platform",
        "docs": "/docs",
        "health": "/health",
        "rules": "/rules",
    }
