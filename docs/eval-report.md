# Eval report

Deterministic offline evaluation: the stub agent + the router over the 20 labeled fixtures, at the shipped posture ($500 envelope: travel **$500** · hardware **$350** · meals **$250** · saas **$200** · other **$100**, confidence **0.80** — see PRODUCT-DILEMMA.md).

**Router-decided: 19/19 match `expected.route`.** 1 fixture(s) are decided by a stateful pipeline stage and shown as _pipeline_.

Shipped **auto-approve** fixtures exercised with no human: **4**.

| Fixture | Expected | Predicted | Result | Rules cited |
|---|---|---|---|---|
| INV-1001 | `auto_approve` | `auto_approve` | ✅ | — |
| INV-1002 | `auto_approve` | `auto_approve` | ✅ | — |
| INV-1003 | `human_review` | `human_review` | ✅ | MEAL-01, MEAL-02, AUTONOMY-CEILING |
| INV-1004 | `human_review` | `human_review` | ✅ | HW-02, AUTONOMY-CEILING |
| INV-1005 | `human_review` | `human_review` | ✅ | GLOBAL-RECEIPT |
| INV-1006 | `human_review` | `human_review` | ✅ | GLOBAL-MATH, HW-02, AUTONOMY-CEILING |
| INV-1007 | `duplicate` | — | _pipeline_ | idempotency at intake |
| INV-1008 | `human_review` | `human_review` | ✅ | GLOBAL-RECEIPT, GLOBAL-VENDOR, GLOBAL-FRAUD, AUTONOMY-CEILING |
| INV-1009 | `human_review` | `human_review` | ✅ | GLOBAL-FX, AUTONOMY-CEILING |
| INV-1010 | `human_review` | `human_review` | ✅ | AUTONOMY-CEILING, AUTONOMY-CONFIDENCE |
| INV-1011 | `human_review` | `human_review` | ✅ | GLOBAL-VENDOR |
| INV-1012 | `human_review` | `human_review` | ✅ | HW-02, AUTONOMY-CEILING |
| INV-1013 | `human_review` | `human_review` | ✅ | SAAS-01, AUTONOMY-CEILING |
| INV-1014A | `human_review` | `human_review` | ✅ | AUTONOMY-CEILING |
| INV-1014B | `human_review` | `human_review` | ✅ | AUTONOMY-CEILING |
| INV-1015 | `reject` | `reject` | ✅ | MEAL-03 |
| INV-1016 | `auto_approve` | `auto_approve` | ✅ | — |
| INV-1017 | `auto_approve` | `auto_approve` | ✅ | — |
| INV-1018 | `human_review` | `human_review` | ✅ | SAAS-01, AUTONOMY-CEILING |
| INV-1019 | `human_review` | `human_review` | ✅ | TRAVEL-02, AUTONOMY-CEILING |

_Regenerate with_ `python eval/run_eval.py`.
