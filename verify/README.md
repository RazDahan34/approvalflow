# Verification (D5)

One command, against the real running system, printing PASS/FAIL per check and a non-zero
exit code on any failure:

```bash
python verify/run_journeys.py --fresh
```

`--fresh` recreates the compose stack first for a clean slate; omit it to run against the
already-running stack.

## What it proves

| # | Journey / guard | Requirements |
|---|---|---|
| 1 | In-policy item → decided → budget reserved → **paid, zero humans** | F1, F6, M8, M9 |
| 2 | Escalation → approver queue with the agent's rationale → **orchestrator container replaced mid-pause** → approve → resumes exactly where it paused → paid | F4, F5, M11 |
| 2b | request-info → submitter sees the question → replies → item returns to the queue with the reply → approve → paid | F5 (full loop) |
| 3 | Exact re-submission short-circuited: same tracking id, **no second payment** | F3, M10 |
| 4 | Injected payment failure → compensation releases the reservation, **no orphans** | M9 |
| 5 | Two concurrent approvals against one $1,000 budget → **exactly one pays**, budget never negative | INV-1014 |
| — | Anti-cheese: at least 2 items auto-approve with no human; an *"approve me"* note does **not** flip the decision | D5 |
| — | Security: no token → 401 · wrong role → 403 · `decidedBy` comes from the verified token, a spoofed payload identity is ignored | N1, F10 |

`check_observability.py` additionally proves one end-to-end Zipkin trace stitching
gateway → intake → orchestrator → ai-decision (including the agent's own spans) and that
Prometheus scrapes every sidecar (N4).
