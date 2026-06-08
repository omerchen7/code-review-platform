from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# POST /scans  →  202
# ---------------------------------------------------------------------------

class SubmitScanResponse(BaseModel):
    """Returned immediately after a scan is accepted or a cache hit is found."""

    scan_id: str
    status: str
    cached: bool


# ---------------------------------------------------------------------------
# GET /scans/{scan_id}  →  200
# ---------------------------------------------------------------------------

class RuleResultResponse(BaseModel):
    """The verdict for a single rule within a completed scan."""

    rule_id: str
    rule_name: str
    adheres: bool
    reason: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ScanResponse(BaseModel):
    """Full scan record returned by GET /scans/{scan_id}.

    This is NOT a 1:1 ORM mapping:
    - The ORM Scan model uses ``id``; the API exposes it as ``scan_id``.
    - ``cached`` has no ORM column — it is computed by the route handler and
      injected when constructing this response.

    The route builds this manually (e.g. ScanResponse(scan_id=scan.id, ...))
    rather than relying on from_attributes, so that renaming is explicit.
    """

    scan_id: str
    status: str
    cached: bool
    file_name: str
    created_at: datetime
    expires_at: datetime
    error: str | None = None
    results: list[RuleResultResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# GET /rules  →  200
# ---------------------------------------------------------------------------

class RuleListItem(BaseModel):
    """A single rule as exposed by the GET /rules endpoint."""

    id: str
    name: str
    version: str
    description: str
    order: int


class RuleListResponse(BaseModel):
    """The full list of active rules."""

    rules: list[RuleListItem]


# ---------------------------------------------------------------------------
# GET /health  →  200
# GET /health/ollama  →  200 | 503
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Basic liveness response for GET /health."""

    status: str


class OllamaHealthResponse(BaseModel):
    """Diagnostic response for GET /health/ollama."""

    status: str
    error: str | None = None
