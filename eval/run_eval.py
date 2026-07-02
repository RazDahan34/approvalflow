"""Offline evaluation harness (B1).

Runs every labeled fixture in sample-invoices.json through the deterministic stub agent
and the router, compares the router's route to the ground-truth `expected.route`, and
writes a committed metrics report to docs/eval-report.md. Exits non-zero on any mismatch
so it can gate CI.

    python eval/run_eval.py
"""

import sys
from pathlib import Path

from approvalflow_common.agent import get_provider
from approvalflow_common.decision import PolicyConfig
from approvalflow_common.evaluation import evaluate_fixtures, load_fixtures

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "sample-invoices.json"
REPORT = ROOT / "docs" / "eval-report.md"


def main() -> int:
    results = evaluate_fixtures(load_fixtures(FIXTURES), get_provider("stub"), PolicyConfig())
    decided = [r for r in results if not r.pipeline]
    passed = sum(r.passed for r in decided)
    total = len(decided)
    autos = sum(1 for r in decided if r.expected == "auto_approve")

    rows = ["| Fixture | Expected | Predicted | Result | Rules cited |", "|---|---|---|---|---|"]
    for r in results:
        if r.pipeline:
            rows.append(f"| {r.id} | `{r.expected}` | — | _pipeline_ | idempotency at intake |")
        else:
            mark = "✅" if r.passed else "❌"
            rows.append(f"| {r.id} | `{r.expected}` | `{r.predicted}` | {mark} | {', '.join(r.rule_ids) or '—'} |")

    report = (
        "# Eval report\n\n"
        "Deterministic offline evaluation: the stub agent + the router over the "
        f"{len(results)} labeled fixtures, at the shipped posture ($500 envelope: travel **$500** · "
        "hardware **$350** · meals **$250** · saas **$200** · other **$100**, confidence **0.80** — "
        "see PRODUCT-DILEMMA.md).\n\n"
        f"**Router-decided: {passed}/{total} match `expected.route`.** "
        f"{len(results) - total} fixture(s) are decided by a stateful pipeline stage and shown as _pipeline_.\n\n"
        f"Shipped **auto-approve** fixtures exercised with no human: **{autos}**.\n\n"
        + "\n".join(rows)
        + "\n\n_Regenerate with_ `python eval/run_eval.py`.\n"
    )
    REPORT.write_text(report, encoding="utf-8")

    for r in results:
        got = "-" if r.pipeline else r.predicted
        status = "pipeline" if r.pipeline else ("PASS" if r.passed else "FAIL")
        print(f"{r.id:10} expected={r.expected:13} got={str(got):13} {status}")
    print(f"\nrouter-decided: {passed}/{total} passed  |  report -> {REPORT.relative_to(ROOT)}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
