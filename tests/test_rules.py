from __future__ import annotations

import textwrap

import pytest

from app.rules import Rule, compute_ruleset_hash, get_rules, load_rules

# ---------------------------------------------------------------------------
# load_rules — loading the real rules file
# ---------------------------------------------------------------------------

def test_load_rules_returns_two_enabled_rules():
    rules = load_rules("rules/rules.yaml")
    assert len(rules) == 2


def test_load_rules_sorted_by_order():
    rules = load_rules("rules/rules.yaml")
    orders = [r.order for r in rules]
    assert orders == sorted(orders)


def test_load_rules_rule_ids():
    rules = load_rules("rules/rules.yaml")
    ids = [r.id for r in rules]
    assert "meaningful_names" in ids
    assert "docstring_accuracy" in ids


def test_get_rules_returns_same_as_load_rules(tmp_path):
    # get_rules() uses lru_cache; clear it so we start fresh.
    get_rules.cache_clear()
    rules_via_load = load_rules("rules/rules.yaml")
    rules_via_get = get_rules()
    assert [r.id for r in rules_via_get] == [r.id for r in rules_via_load]
    get_rules.cache_clear()


# ---------------------------------------------------------------------------
# compute_ruleset_hash
# ---------------------------------------------------------------------------

def test_ruleset_hash_is_64_char_hex():
    rules = load_rules("rules/rules.yaml")
    h = compute_ruleset_hash(rules)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_ruleset_hash_is_stable():
    rules = load_rules("rules/rules.yaml")
    assert compute_ruleset_hash(rules) == compute_ruleset_hash(rules)


def test_ruleset_hash_changes_on_prompt_change():
    rules = load_rules("rules/rules.yaml")
    original_hash = compute_ruleset_hash(rules)

    modified = [
        Rule(**{**rules[0].model_dump(), "prompt_template": "completely different {code}"}),
        *rules[1:],
    ]
    assert compute_ruleset_hash(modified) != original_hash


def test_ruleset_hash_changes_on_version_bump():
    rules = load_rules("rules/rules.yaml")
    original_hash = compute_ruleset_hash(rules)

    modified = [
        Rule(**{**rules[0].model_dump(), "version": "2.0"}),
        *rules[1:],
    ]
    assert compute_ruleset_hash(modified) != original_hash


def test_ruleset_hash_changes_on_order_change():
    rules = load_rules("rules/rules.yaml")
    reversed_rules = list(reversed(rules))
    assert compute_ruleset_hash(rules) != compute_ruleset_hash(reversed_rules)


def test_ruleset_hash_unchanged_by_description_change():
    """Description is excluded from the hash; changing it must not affect it."""
    rules = load_rules("rules/rules.yaml")
    original_hash = compute_ruleset_hash(rules)

    modified = [
        Rule(**{**rules[0].model_dump(), "description": "totally new description"}),
        *rules[1:],
    ]
    assert compute_ruleset_hash(modified) == original_hash


# ---------------------------------------------------------------------------
# load_rules — validation: duplicate ids
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path, content: str):
    p = tmp_path / "rules.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


def test_duplicate_enabled_rule_ids_raise(tmp_path):
    path = _write_yaml(tmp_path, """\
        rules:
          - id: rule_a
            name: "Rule A"
            version: "1.0"
            description: "desc"
            prompt_template: "check {code}"
            enabled: true
            order: 1
          - id: rule_a
            name: "Rule A Copy"
            version: "1.0"
            description: "desc"
            prompt_template: "check {code}"
            enabled: true
            order: 2
    """)
    with pytest.raises(ValueError, match="duplicate ids"):
        load_rules(path)


def test_disabled_rule_does_not_trigger_duplicate_id_check(tmp_path):
    """A duplicate id is only an error when both rules are enabled."""
    path = _write_yaml(tmp_path, """\
        rules:
          - id: rule_a
            name: "Rule A"
            version: "1.0"
            description: "desc"
            prompt_template: "check {code}"
            enabled: true
            order: 1
          - id: rule_a
            name: "Rule A Disabled"
            version: "1.0"
            description: "desc"
            prompt_template: "check {code}"
            enabled: false
            order: 2
    """)
    rules = load_rules(path)
    assert len(rules) == 1


# ---------------------------------------------------------------------------
# load_rules — validation: duplicate order values
# ---------------------------------------------------------------------------

def test_duplicate_enabled_order_values_raise(tmp_path):
    path = _write_yaml(tmp_path, """\
        rules:
          - id: rule_a
            name: "Rule A"
            version: "1.0"
            description: "desc"
            prompt_template: "check {code}"
            enabled: true
            order: 1
          - id: rule_b
            name: "Rule B"
            version: "1.0"
            description: "desc"
            prompt_template: "check {code}"
            enabled: true
            order: 1
    """)
    with pytest.raises(ValueError, match="duplicate order"):
        load_rules(path)


def test_missing_rules_key_raises(tmp_path):
    path = _write_yaml(tmp_path, """\
        something_else:
          - id: rule_a
    """)
    with pytest.raises(ValueError, match="top-level 'rules' key"):
        load_rules(path)
