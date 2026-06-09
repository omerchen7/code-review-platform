# Code Review Platform — Local POC 🧠

A local, asynchronous, LLM-powered Python code review API. Upload a `.py` file, get back structured rule verdicts produced by a local Ollama model. Everything runs locally with no cloud services or remote model provider.

---

## Table of Contents

1. [Project Overview 🧭](#1-project-overview)
2. [Requirements Coverage ✅](#2-requirements-coverage)
3. [Architecture 🏗️](#3-architecture)
4. [Setup ⚙️](#4-setup)
5. [Running the App 🚀](#5-running-the-app)
6. [API Examples 🔌](#6-api-examples)
7. [Running Tests 🧪](#7-running-tests)
8. [Design Decisions 💡](#8-design-decisions)
9. [Known Limitations and Future Improvements ⚠️](#9-known-limitations-and-future-improvements)
10. [AI Usage 🤖](#10-ai-usage)

---

## 1. Project Overview 🧭

This is a **proof-of-concept** code review platform that:

- Accepts a Python source file via `POST /scans`.
- Runs each file through a set of predefined YAML-configured review rules, each evaluated by a local Ollama LLM.
- Returns a `scan_id` immediately; the scan runs in the background.
- Exposes `GET /scans/{scan_id}` to fetch the full result including a `TRUE/FALSE` verdict and a brief reason for every rule.
- Stores results in a local SQLite database for 24 hours, then expires them.
- Reuses existing results based on file content, active ruleset, provider, model, and generation configuration to avoid redundant LLM calls.
- Enforces a limit of 5 parallel scans; a 6th request is rejected immediately with HTTP 429.

Everything runs locally. No cloud assets, no authentication, no message queues.

---

## 2. Requirements Coverage ✅

| Requirement | Implementation |
|---|---|
| Async scan request / result fetch via separate endpoints | `POST /scans` returns `scan_id` immediately; result fetched via `GET /scans/{scan_id}` |
| TRUE / FALSE result per rule | `adheres: bool` field on every `RuleResult`, with an optional `reason` string |
| Up to 5 parallel scans | `ConcurrencyManager` with a non-blocking `try_acquire()` gate |
| 6th scan rejected immediately | `try_acquire()` returns `False` — HTTP 429, no queuing |
| Local SQLite persistence | SQLAlchemy with `sqlite:///./code_review.db` |
| 24-hour result retention / expiration | `expires_at` column; expired rows return HTTP 410 and are not reused |
| Result reuse / cache | SHA-256 cache key over `(content_hash, ruleset_hash, provider, model, temperature)` |
| Local LLM only — Ollama | `OllamaProvider` calls `http://localhost:11434/api/generate` via `httpx` |
| Configurable provider / model | `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_TEMPERATURE` in `.env` |

---

## 3. Architecture 🏗️

```
POST /scans  ->  validate  ->  cache lookup
                               |  hit: return cached scan_id
                               |  miss: acquire slot
                               +->  create Scan row
                                    asyncio.create_task
                                    +->  run_scan() [background]

GET /scans/{id}  ->  return Scan + RuleResults
```

| Component | File(s) | Responsibility |
|---|---|---|
| API layer | `app/api/routes.py` | Request validation, cache check, concurrency gate, response shaping |
| App wiring | `app/main.py` | Lifespan events, stale-scan recovery, background cleanup task |
| Rules loader | `app/rules.py` | Load and validate YAML rules, compute `ruleset_hash` |
| LLM abstraction | `app/llm/` | `BaseLLMProvider` interface, `OllamaProvider`, `factory.py` |
| Scanner | `app/scanner.py` | Background task: evaluate rules, persist `RuleResult` rows, update status |
| Persistence | `app/db.py`, `app/models.py` | SQLAlchemy engine, `Scan` and `RuleResult` ORM models |
| Cache | `app/cache.py` | `compute_cache_key()`, `lookup_cached_scan()` |
| Concurrency | `app/concurrency.py` | `ConcurrencyManager` — non-blocking admit/reject gate |
| Config | `app/config.py` | Pydantic-settings, reads `.env` |
| Schemas | `app/schemas.py` | Pydantic DTOs for API request/response |

---

## 4. Setup ⚙️

### Prerequisites 📋

- Python 3.11 or later
- [Ollama](https://ollama.com) installed and running locally

### 1. Clone and create a virtual environment 🐍

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies 📦

```powershell
pip install -e ".[dev]"
```

### 3. Install and start Ollama 🦙

Download from [ollama.com](https://ollama.com) and follow the installer.
Verify it is running:

```powershell
ollama list
```

### 4. Pull the default model 🧠

```powershell
ollama pull qwen2.5-coder:7b
```

Lighter alternatives if hardware is constrained:

```powershell
ollama pull qwen2.5-coder:3b
ollama pull llama3.2:3b
```

Update `LLM_MODEL` in `.env` if you choose a different model.

### 5. Configure environment (optional) 🔧

```powershell
copy .env.example .env
```

The defaults in `.env.example` work out of the box with a local Ollama installation.
Edit `.env` only if you need a different model, base URL, or TTL.

---

## 5. Running the App 🚀

```powershell
uvicorn app.main:app --reload
```

> **Important:** Always run with a **single worker** (the default).
> The concurrency gate is an in-process counter; running multiple workers would give each worker its own independent counter and the 5-scan limit would not be enforced globally.
> See [Known Limitations](#9-known-limitations-and-future-improvements).

| URL | Purpose |
|---|---|
| `http://localhost:8000/docs` | Interactive Swagger UI |
| `http://localhost:8000/health` | Liveness check |
| `http://localhost:8000/health/ollama` | Ollama reachability probe |

---

## 6. API Examples 🔌

### Submit a scan 📤

```bash
curl -X POST http://localhost:8000/scans \
  -F "file=@your_script.py"
```

Response (`202 Accepted`):

```json
{
  "scan_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "pending",
  "cached": false
}
```

If an identical file was already scanned with the same ruleset and model, `"cached": true` is returned and no new LLM calls are made. The scan_id will point to the existing result.

### Fetch results 📥

```bash
curl http://localhost:8000/scans/3fa85f64-5717-4562-b3fc-2c963f66afa6
```

Response (`200 OK`) once the scan completes:

```json
{
  "scan_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "completed",
  "cached": false,
  "file_name": "your_script.py",
  "created_at": "2026-06-08T10:00:00",
  "expires_at": "2026-06-09T10:00:00",
  "error": null,
  "results": [
    {
      "rule_id": "meaningful_names",
      "rule_name": "Meaningful Variable Names",
      "adheres": true,
      "reason": "All identifiers are descriptive and self-explanatory."
    },
    {
      "rule_id": "docstring_accuracy",
      "rule_name": "Docstring Accuracy",
      "adheres": false,
      "reason": "The docstring for 'process' states it returns a sorted list, but the implementation returns a filtered one."
    }
  ]
}
```

**`GET /scans/{scan_id}` status codes:**

| Code | Meaning |
|---|---|
| `200` | Scan found and not expired (any status) |
| `404` | Unknown `scan_id` |
| `410 Gone` | Scan results have expired (older than 24 h) |

### List active rules 📜

```bash
curl http://localhost:8000/rules
```

### Health checks ❤️

```bash
# Application liveness — always 200 while the process is up
curl http://localhost:8000/health

# Ollama reachability — 503 if Ollama is not running
curl http://localhost:8000/health/ollama
```

### Capacity limit 🚦

When 5 scans are already running, the next `POST /scans` returns:

```
HTTP 429 Too Many Requests
```

```json
{
  "detail": "Maximum parallel scan capacity reached (5 scans). Please retry when a scan completes."
}
```

---

## 7. Running Tests 🧪

Tests use fake LLM providers and an in-memory SQLite database.
**Ollama does not need to be running.**

```powershell
# Run the full test suite
pytest

# Verbose output
pytest -v

# Single module
pytest tests/test_api.py -v
```

The suite covers:

- **Unit tests** — rules loader (`test_rules.py`), cache key and lookup (`test_cache.py`), concurrency manager (`test_concurrency.py`)
- **Scanner tests** — `run_scan` called directly with a `FakeLLMProvider` and isolated in-memory SQLite (`test_scanner.py`)
- **API integration tests** — full HTTP lifecycle via `httpx.AsyncClient` with a fake provider, dependency overrides, and an isolated in-memory database (`test_api.py`)

---

## 8. Design Decisions 💡

**FastAPI** — Native `async/await` support makes it natural to launch background tasks with `asyncio.create_task`. The automatic OpenAPI docs at `/docs` let a reviewer explore the API without extra tooling.

**SQLite** — Zero-configuration, file-based persistence that is appropriate for a local POC. A single `DB_URL` change is all that is needed to point at PostgreSQL later.

**SQLAlchemy + Pydantic** — SQLAlchemy provides the ORM and cascade deletes (expired `Scan` rows take their `RuleResult` children with them automatically). Pydantic-settings handles typed configuration from `.env`; Pydantic models enforce the API contract at the boundary.

**YAML rules** — Keeping rules in a versioned YAML file means a reviewer can read, add, or modify rules without touching Python code. The `ruleset_hash` (SHA-256 over rule IDs, names, versions, prompt templates, and evaluation order) ensures that any rule change automatically invalidates old cached results.

**httpx for Ollama transport** — `httpx.AsyncClient` integrates naturally with the async FastAPI event loop, avoiding thread-pool overhead for LLM calls.

**In-process `ConcurrencyManager` instead of a semaphore** — `asyncio.Semaphore.acquire()` queues callers silently. The assignment requires *immediate* rejection of a 6th scan. `ConcurrencyManager.try_acquire()` checks the counter non-blockingly and returns `False` straight away if capacity is full, allowing the route to respond with HTTP 429 without delay. The trade-off is that this only works correctly under a single process, which is documented clearly.

**Cache key includes file hash, ruleset hash, provider, model, and temperature** — Any change to the input code, the rules, or the LLM configuration could change the output, so all five components must be part of the key. Temperature is included because it is an output-affecting model parameter. Keeping it in the cache key prevents reusing results across different generation configurations.

**Expiration via `expires_at` plus cleanup** — Each `Scan` row stores an absolute expiry timestamp set at creation time (`created_at + TTL`). Validity is always checked at read time (GET and cache lookup), so results never slip through after expiry regardless of when the cleanup task last ran. The background cleanup task deletes rows where `expires_at <= now` once per hour and is purely for space reclamation.

---

## 9. Known Limitations and Future Improvements ⚠️

| Limitation | Notes |
|---|---|
| **Single-process concurrency gate** | The 5-scan limit is enforced per process. Running `uvicorn --workers 2` would allow up to 10 concurrent scans. For multi-worker or multi-host deployments, replace `ConcurrencyManager` with a Redis counter or a proper task queue. |
| **No authentication or multi-tenancy** | All callers share the same scan namespace and result store. |
| **No distributed task queue** | Scans run as `asyncio` tasks inside the web process. For production, use Celery, RQ, or a similar queue so scans survive web-process restarts and can be distributed across workers. |
| **No database migrations** | Tables are created with `Base.metadata.create_all` on startup. Schema changes require manual intervention. Use Alembic for any schema evolution. |
| **Single-file scans only** | One `.py` file per scan request. Multi-file or project-level review is not supported. |
| **No frontend** | Results are consumed via the JSON API or the Swagger UI at `/docs`. |
| **Production path** | PostgreSQL, Redis + Celery/RQ, Alembic, role-based auth, structured logging, Prometheus metrics, object storage for large files. |

---

## 10. AI Usage 🤖

AI tools (a large language model assistant in the Cursor IDE) were used throughout this project as an engineering assistant:

- **Architecture planning** — proposing and critiquing the project structure, API design, database schema, caching strategy, and concurrency approach before any code was written.
- **Incremental implementation** — generating each module step by step with explicit requirements and constraints provided per step, reviewed and accepted or corrected before proceeding.
- **Code review** — applying targeted corrections per step (nullable fields, mutable defaults, canonical JSON hashing, encoding fixes, import cleanup, resource release guarantees).
- **Test authoring** — writing the unit and integration test suite, including identifying the `StaticPool` issue with in-memory SQLite across thread boundaries and the `asyncio` background-task polling pattern.

The full prompt and conversation transcript is included separately with this submission as required.