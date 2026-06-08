from __future__ import annotations

import ast
import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.cache import lookup_cached_scan
from app.config import Settings, get_settings
from app.concurrency import ConcurrencyManager
from app.db import get_db
from app.llm.base import BaseLLMProvider
from app.models import Scan
from app.rules import Rule, compute_ruleset_hash, get_rules
from app.scanner import run_scan
from app.schemas import (
    HealthResponse,
    OllamaHealthResponse,
    RuleListItem,
    RuleListResponse,
    RuleResultResponse,
    ScanResponse,
    SubmitScanResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependency helpers — pull shared objects from app.state
# ---------------------------------------------------------------------------

def _get_provider(request: Request) -> BaseLLMProvider:
    return request.app.state.provider


def _get_concurrency_manager(request: Request) -> ConcurrencyManager:
    return request.app.state.concurrency_manager


def _get_active_rules(request: Request) -> list[Rule]:
    return request.app.state.rules


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# POST /scans
# ---------------------------------------------------------------------------

@router.post("/scans", response_model=SubmitScanResponse, status_code=202)
async def submit_scan(
    file: UploadFile,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    provider: BaseLLMProvider = Depends(_get_provider),
    concurrency_manager: ConcurrencyManager = Depends(_get_concurrency_manager),
    rules: list[Rule] = Depends(_get_active_rules),
) -> SubmitScanResponse:
    """Accept a Python source file for code review.

    Returns 202 in all acceptance cases (new scan and cache hit alike).
    The ``cached`` flag indicates whether a prior result is being reused.
    Use GET /scans/{scan_id} to retrieve the full results.
    """
    # ------------------------------------------------------------------
    # 1. Validate the uploaded file
    # ------------------------------------------------------------------
    file_name = file.filename or ""
    if not file_name.endswith(".py"):
        raise HTTPException(status_code=400, detail="Only .py files are accepted.")

    raw_bytes: bytes = await file.read()

    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(raw_bytes) > settings.max_file_bytes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File exceeds the maximum allowed size of "
                f"{settings.max_file_bytes} bytes."
            ),
        )

    try:
        code = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400, detail="File is not valid UTF-8."
        )

    try:
        ast.parse(code)
    except SyntaxError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"File is not valid Python: {exc}",
        )

    # ------------------------------------------------------------------
    # 2. Compute hashes
    # ------------------------------------------------------------------
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    ruleset_hash = compute_ruleset_hash(rules)

    # ------------------------------------------------------------------
    # 3. Cache lookup — no slot consumed on a hit
    # ------------------------------------------------------------------
    cached_scan = lookup_cached_scan(
        db=db,
        content_hash=content_hash,
        ruleset_hash=ruleset_hash,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        llm_temperature=settings.llm_temperature,
    )
    if cached_scan is not None:
        logger.info("Cache hit for scan '%s'.", cached_scan.id)
        return SubmitScanResponse(
            scan_id=cached_scan.id,
            status=cached_scan.status,
            cached=True,
        )

    # ------------------------------------------------------------------
    # 4. Concurrency gate — reject immediately if at capacity
    # ------------------------------------------------------------------
    acquired = await concurrency_manager.try_acquire()
    if not acquired:
        raise HTTPException(
            status_code=429,
            detail=(
                "Maximum parallel scan capacity reached "
                f"({settings.max_parallel_scans} scans). "
                "Please retry when a scan completes."
            ),
        )

    # ------------------------------------------------------------------
    # 5. Create Scan row and launch background task
    #    If anything fails between acquiring the slot and launching the
    #    task, release the slot before re-raising.
    # ------------------------------------------------------------------
    try:
        now = _utcnow()
        scan = Scan(
            id=_new_scan_id(),
            file_name=file_name,
            content_hash=content_hash,
            ruleset_hash=ruleset_hash,
            llm_provider=settings.llm_provider,
            llm_model=settings.llm_model,
            llm_temperature=settings.llm_temperature,
            status="pending",
            error=None,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=settings.result_ttl_hours),
        )
        db.add(scan)
        db.commit()
        db.refresh(scan)

        asyncio.create_task(
            run_scan(
                scan_id=scan.id,
                code=code,
                rules=rules,
                provider=provider,
                concurrency_manager=concurrency_manager,
            )
        )

    except Exception:
        await concurrency_manager.release()
        raise

    return SubmitScanResponse(
        scan_id=scan.id,
        status=scan.status,
        cached=False,
    )


def _new_scan_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# GET /scans/{scan_id}
# ---------------------------------------------------------------------------

@router.get("/scans/{scan_id}", response_model=ScanResponse)
def get_scan(
    scan_id: str,
    db: Session = Depends(get_db),
) -> ScanResponse:
    """Return the current state and results of a scan.

    - 200: scan exists and has not expired (any status).
    - 404: scan_id is not known.
    - 410: scan existed but its results have expired.
    """
    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found.")

    now = _utcnow()
    if scan.expires_at <= now:
        raise HTTPException(
            status_code=410,
            detail="Scan results have expired and are no longer available.",
        )

    results = [
        RuleResultResponse.model_validate(r) for r in scan.results
    ]

    return ScanResponse(
        scan_id=scan.id,
        status=scan.status,
        cached=False,
        file_name=scan.file_name,
        created_at=scan.created_at,
        expires_at=scan.expires_at,
        error=scan.error,
        results=results,
    )


# ---------------------------------------------------------------------------
# GET /rules
# ---------------------------------------------------------------------------

@router.get("/rules", response_model=RuleListResponse)
def list_rules(
    rules: list[Rule] = Depends(_get_active_rules),
) -> RuleListResponse:
    """Return the list of active rules without exposing prompt templates."""
    return RuleListResponse(
        rules=[
            RuleListItem(
                id=rule.id,
                name=rule.name,
                version=rule.version,
                description=rule.description,
                order=rule.order,
            )
            for rule in rules
        ]
    )


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Simple process liveness check. Always returns 200 if the app is running."""
    return HealthResponse(status="ok")


# ---------------------------------------------------------------------------
# GET /health/ollama
# ---------------------------------------------------------------------------

@router.get("/health/ollama", response_model=OllamaHealthResponse)
async def health_ollama(
    settings: Settings = Depends(get_settings),
) -> OllamaHealthResponse:
    """Diagnostic probe for the Ollama server.

    Returns 200 when Ollama is reachable, 503 otherwise.
    This endpoint is independent of app liveness (GET /health).
    """
    base_url = settings.llm_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{base_url}/api/tags")
        response.raise_for_status()
        return OllamaHealthResponse(status="ok")
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content=OllamaHealthResponse(status="unreachable", error=str(exc)).model_dump(),
        )
