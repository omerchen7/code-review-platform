from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# POST /scans  →  202
# ---------------------------------------------------------------------------

class SubmitScanResponse(BaseModel):
    """Returned immediately after a scan is accepted or a cache hit is found."""

    scan_id: str = Field(description="UUID of the created (or reused) scan.")
    status: str = Field(description="Initial status: `pending` for new scans, `completed` for cache hits.")
    cached: bool = Field(description="`true` if a prior result was reused; no new LLM calls were made.")


# ---------------------------------------------------------------------------
# GET /scans/{scan_id}  →  200
# ---------------------------------------------------------------------------

class RuleResultResponse(BaseModel):
    """The verdict for a single rule within a completed scan."""

    rule_id: str = Field(description="Stable identifier for the rule (e.g. `meaningful_names`).")
    rule_name: str = Field(description="Human-readable rule name.")
    adheres: bool = Field(description="`true` if the code satisfies this rule, `false` otherwise.")
    reason: str | None = Field(default=None, description="Brief explanation of the verdict from the LLM.")

    model_config = ConfigDict(from_attributes=True)


class ScanResponse(BaseModel):
    """Full scan record returned by GET /scans/{scan_id}.

    This is NOT a 1:1 ORM mapping:
    - The ORM Scan model uses ``id``; the API exposes it as ``scan_id``.

    The route builds this manually (e.g. ScanResponse(scan_id=scan.id, ...))
    rather than relying on from_attributes, so that renaming is explicit.

    The ``cached`` flag is intentionally absent here. It is meaningful only for
    ``POST /scans`` (which tells the caller whether a fresh scan was started or
    an existing result was reused). ``GET /scans/{scan_id}`` returns the stored
    scan resource itself — cache semantics belong to the submit response.
    """

    scan_id: str = Field(description="UUID of the scan.")
    status: str = Field(description="One of: `pending`, `running`, `completed`, `failed`.")
    file_name: str = Field(description="Original filename of the uploaded source file.")
    created_at: datetime = Field(description="UTC timestamp when the scan was created.")
    expires_at: datetime = Field(description="UTC timestamp after which the result is no longer available (HTTP 410).")
    error: str | None = Field(default=None, description="Error message if `status` is `failed`.")
    results: list[RuleResultResponse] = Field(default_factory=list, description="Rule verdicts (populated once `status` is `completed`).")


# ---------------------------------------------------------------------------
# GET /rules  →  200
# ---------------------------------------------------------------------------

class RuleListItem(BaseModel):
    """A single rule as exposed by the GET /rules endpoint."""

    id: str = Field(description="Stable rule identifier (matches `rule_id` in scan results).")
    name: str = Field(description="Human-readable rule name.")
    version: str = Field(description="Rule version; a change here invalidates cached results.")
    description: str = Field(description="Plain-English description of what the rule checks.")
    order: int = Field(description="Evaluation order (ascending). Rules run in this sequence.")


class RuleListResponse(BaseModel):
    """The full list of active rules."""

    rules: list[RuleListItem]


# ---------------------------------------------------------------------------
# GET /health  →  200
# GET /health/ollama  →  200 | 503
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Basic liveness response for GET /health."""

    status: str = Field(description="Always `ok` while the process is running.")


class OllamaHealthResponse(BaseModel):
    """Diagnostic response for GET /health/ollama."""

    status: str = Field(description="`ok` if Ollama is reachable, `unreachable` otherwise.")
    error: str | None = Field(default=None, description="Error detail when Ollama is unreachable.")
