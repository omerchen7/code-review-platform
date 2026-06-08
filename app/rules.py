from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from app.config import get_settings


class Rule(BaseModel):
    """A single code-review rule loaded from rules.yaml."""

    id: str
    name: str
    version: str
    description: str
    prompt_template: str
    enabled: bool
    order: int


def load_rules(path: str) -> list[Rule]:
    """Load, validate, and return enabled rules sorted by evaluation order.

    Only rules with ``enabled: true`` are returned. The returned list is sorted
    by ``order`` ascending, which also determines the evaluation sequence and is
    included in the ruleset hash.

    Raises:
        FileNotFoundError: if the YAML file does not exist at ``path``.
        ValueError: if the YAML is missing the top-level ``rules`` key, or if
            any rule entry fails Pydantic validation.
    """
    with open(path, encoding="utf-8") as fh:
        raw: Any = yaml.safe_load(fh)

    if not isinstance(raw, dict) or "rules" not in raw:
        raise ValueError(
            f"Invalid rules file '{path}': expected a top-level 'rules' key."
        )

    raw_rules: Any = raw["rules"]
    if not isinstance(raw_rules, list):
        raise ValueError(
            f"Invalid rules file '{path}': 'rules' must be a list."
        )

    rules: list[Rule] = []
    errors: list[str] = []
    for index, entry in enumerate(raw_rules):
        try:
            rule = Rule.model_validate(entry)
        except ValidationError as exc:
            errors.append(f"Rule at index {index}: {exc}")
            continue
        if rule.enabled:
            rules.append(rule)

    if errors:
        raise ValueError(
            f"Rules file '{path}' contains invalid entries:\n" + "\n".join(errors)
        )

    rules.sort(key=lambda r: r.order)

    seen_ids: set[str] = set()
    duplicate_ids = [r.id for r in rules if r.id in seen_ids or seen_ids.add(r.id)]  # type: ignore[func-returns-value]
    if duplicate_ids:
        raise ValueError(
            f"Rules file '{path}' contains enabled rules with duplicate ids: "
            + ", ".join(sorted(set(duplicate_ids)))
        )

    seen_orders: set[int] = set()
    duplicate_orders = [r.order for r in rules if r.order in seen_orders or seen_orders.add(r.order)]  # type: ignore[func-returns-value]
    if duplicate_orders:
        raise ValueError(
            f"Rules file '{path}' contains enabled rules with duplicate order values: "
            + ", ".join(str(o) for o in sorted(set(duplicate_orders)))
        )

    return rules


def compute_ruleset_hash(rules: list[Rule]) -> str:
    """Return a SHA-256 hex digest that uniquely identifies this set of rules.

    Only the fields that affect LLM output are included:
    ``order``, ``id``, ``name``, ``version``, and ``prompt_template``.

    The ``description`` field is intentionally excluded because it is never
    sent to the model and does not influence the result.

    The fingerprint is a canonical JSON array (``sort_keys=True``,
    no extra whitespace) where each element is a dict with the five
    output-affecting fields. Using JSON avoids ambiguity that manual string
    concatenation can introduce (e.g. a pipe character appearing inside a
    field value).

    Any change to a prompt, version bump, reorder, enable/disable, or addition
    of a new rule produces a different hash, automatically invalidating cached
    scan results that were produced with the old ruleset.
    """
    fingerprint_data = [
        {
            "order": rule.order,
            "id": rule.id,
            "name": rule.name,
            "version": rule.version,
            "prompt_template": rule.prompt_template,
        }
        for rule in rules
    ]
    fingerprint = json.dumps(fingerprint_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


@lru_cache
def get_rules() -> list[Rule]:
    """Return the active rules for the process lifetime.

    Reads ``rules_path`` from settings and caches the result so the YAML file
    is parsed exactly once per process. Call ``get_rules.cache_clear()`` in
    tests that need a fresh load.
    """
    settings = get_settings()
    return load_rules(settings.rules_path)
