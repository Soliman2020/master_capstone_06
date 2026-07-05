"""Tests for governance/policy.py.

The point of this file is not just "policy works" — it's that the *same*
constraint evaluator handles two different domains. P6's spend cap and a
synthetic P7-style risk-band threshold both flow through ``evaluate_constraints``
/ ``Policy.evaluate`` unchanged. That is the P7-transfer linchpin.
"""

import sys
from pathlib import Path

# Allow running ``pytest tests/`` from the project root without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from governance.policy import (  # noqa: E402
    Policy,
    ReviewDecision,
    evaluate_constraints,
)


def _p6_policy():
    return Policy.from_dict(
        {
            "domain": "property_management",
            "actions": [
                {
                    "name": "maintenance.schedule",
                    "side_effect": True,
                    "allow": True,
                    "constraints": {"spend_cap_usd": {"le": 500}},
                    "require_fields": ["unit_id", "vendor", "cost_estimate"],
                },
                {
                    "name": "tenant.query",
                    "side_effect": False,
                    "allow": True,
                },
                {
                    "name": "tenant.evict",
                    "side_effect": True,
                    "allow": False,
                    "require_human": True,
                    "block_reason": "outside agent authority",
                },
            ],
        }
    )


def _p7_style_policy():
    """A SOC policy reusing the SAME mechanism. Proves domain-agnosticism."""
    return Policy.from_dict(
        {
            "domain": "security_operations",
            "actions": [
                {
                    "name": "incident.escalate",
                    "side_effect": True,
                    "allow": True,
                    "require_human": True,
                    "constraints": {"risk_band_score": {"ge": 75}},
                    "require_fields": ["incident_id", "risk_band_score"],
                },
                {
                    "name": "incident.fuse",
                    "side_effect": False,
                    "allow": True,
                },
            ],
        }
    )


def test_evaluate_constraints_shared_mechanism():
    # P6 spend cap and P7 risk band are the same predicate machinery.
    p6 = evaluate_constraints({"spend_cap_usd": 300}, {"spend_cap_usd": {"le": 500}})
    assert p6 == []
    p6_over = evaluate_constraints({"spend_cap_usd": 600}, {"spend_cap_usd": {"le": 500}})
    assert p6_over == ["constraint_failed:spend_cap_usd:le:500"]

    p7 = evaluate_constraints({"risk_band_score": 80}, {"risk_band_score": {"ge": 75}})
    assert p7 == []
    p7_low = evaluate_constraints({"risk_band_score": 50}, {"risk_band_score": {"ge": 75}})
    assert p7_low == ["constraint_failed:risk_band_score:ge:75"]


def test_p6_maintenance_under_cap_allowed():
    p = _p6_policy()
    d = p.evaluate(
        "maintenance.schedule",
        {"unit_id": "4B", "vendor": "AC Co", "cost_estimate": 300, "spend_cap_usd": 300},
    )
    assert d.allow and d.route == "allow"


def test_p6_maintenance_over_cap_blocked_and_human():
    p = _p6_policy()
    d = p.evaluate(
        "maintenance.schedule",
        {"unit_id": "4B", "vendor": "AC Co", "cost_estimate": 600, "spend_cap_usd": 600},
    )
    assert not d.allow
    assert d.route == "block"  # fail closed for side-effect constraint breach
    assert "constraint_failed:spend_cap_usd:le:500" in d.violations


def test_p6_maintenance_missing_required_field_blocked():
    p = _p6_policy()
    d = p.evaluate("maintenance.schedule", {"unit_id": "4B"})  # no vendor/cost
    assert not d.allow
    assert "missing_required_field:vendor" in d.violations
    assert "missing_required_field:cost_estimate" in d.violations


def test_p6_readonly_autallow():
    p = _p6_policy()
    d = p.evaluate("tenant.query", {"unit_id": "4B"})
    assert d.allow and d.route == "allow" and d.require_human is False


def test_p6_eviction_hard_block():
    p = _p6_policy()
    d = p.evaluate("tenant.evict", {"tenant_id": "T-0042"})
    assert not d.allow
    assert d.violations == ["action_not_allowed"]
    assert d.require_human is True
    assert "outside agent authority" in d.reason


def test_p7_escalate_high_risk_requires_human():
    p = _p7_style_policy()
    d = p.evaluate(
        "incident.escalate",
        {"incident_id": "INC-0001", "risk_band_score": 90},
    )
    assert d.allow and d.route == "require_human" and d.require_human is True


def test_p7_escalate_low_risk_blocked():
    p = _p7_style_policy()
    d = p.evaluate(
        "incident.escalate",
        {"incident_id": "INC-0001", "risk_band_score": 40},
    )
    assert not d.allow
    assert "constraint_failed:risk_band_score:ge:75" in d.violations


def test_unknown_action_blocked():
    p = _p6_policy()
    d = p.evaluate("tenant.bakecake", {})
    assert not d.allow and d.violations == ["unknown_action"]


def test_route_property_enum():
    assert ReviewDecision(allow=True, require_human=False).route == "allow"
    assert ReviewDecision(allow=True, require_human=True).route == "require_human"
    assert ReviewDecision(allow=False, require_human=True).route == "block"
    assert ReviewDecision(allow=False, require_human=False).route == "block"