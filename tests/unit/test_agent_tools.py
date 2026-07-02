"""Unit tests for the agent's toolbox — the logic behind the MCP server (B2)."""

import json
from pathlib import Path

from approvalflow_common.agent_tools import AgentToolbox
from approvalflow_common.decision import PolicyConfig
from approvalflow_common.policy_rag import PolicyIndex

INDEX = PolicyIndex.from_file(Path(__file__).resolve().parents[2] / "policy.md")
TOOLBOX = AgentToolbox(INDEX, {"Bistro 19", "Dell"}, PolicyConfig())


def test_fetch_policy_by_category():
    clauses = json.loads(TOOLBOX.fetch_policy(category="meals"))["clauses"]
    ids = {c["rule_id"] for c in clauses}
    assert {"MEAL-01", "MEAL-02", "MEAL-03"} <= ids
    assert "TRAVEL-02" not in ids


def test_fetch_policy_by_query():
    clauses = json.loads(TOOLBOX.fetch_policy(query="alcohol receipts"))["clauses"]
    assert "MEAL-03" in {c["rule_id"] for c in clauses}


def test_fetch_policy_defaults_to_globals():
    clauses = json.loads(TOOLBOX.fetch_policy())["clauses"]
    assert all(c["rule_id"].startswith("GLOBAL-") for c in clauses)


def test_fetch_policy_rejects_unknown_category():
    assert "error" in json.loads(TOOLBOX.fetch_policy(category="yachts"))


def test_lookup_vendor_is_case_insensitive():
    assert json.loads(TOOLBOX.lookup_vendor("bistro 19"))["known"] is True
    assert json.loads(TOOLBOX.lookup_vendor("QuickPay LLC"))["known"] is False


def test_thresholds_reflect_the_posture():
    posture = json.loads(TOOLBOX.get_autonomy_thresholds())
    assert posture["envelope_usd"] == 500.0
    assert posture["category_ceilings_usd"]["other"] == 100.0
    assert posture["confidence_threshold"] == 0.80
