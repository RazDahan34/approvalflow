"""N4 verification: one end-to-end trace + live metrics.

Submits an invoice, then asserts that Zipkin holds a SINGLE trace whose spans cover
the gateway, intake, orchestrator and the ai-decision app (the agent's own spans),
and that Prometheus is scraping the sidecars.

    python verify/check_observability.py     (stack must be up)
"""

import sys
import time
import uuid

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
GATEWAY = "http://localhost:8080"
ZIPKIN = "http://localhost:9411"
PROMETHEUS = "http://localhost:9091"

client = httpx.Client(timeout=15)


def main() -> int:
    token = client.post(
        f"{GATEWAY}/auth/token", json={"subject": "otel.check", "role": "submitter"}
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    body = {
        "submitter": "otel@northwind.example", "department": "engineering-2026Q2",
        "vendor": "Bistro 19", "vendorKnown": True,
        "invoiceNumber": f"OTEL-{uuid.uuid4().hex[:8]}", "currency": "USD",
        "category": "meals", "attendees": 1,
        "lineItems": [{"description": "Team lunch", "quantity": 1, "unitPrice": 42.0}],
        "taxAmount": 0.0, "total": 42.0, "receiptPresent": True, "date": "2026-05-12",
    }
    tracking_id = client.post(f"{GATEWAY}/invoices", json=body, headers=headers).json()["trackingId"]
    print(f"submitted {tracking_id}; waiting for processing + span export...")

    deadline = time.time() + 60
    best: tuple[int, set] = (0, set())
    while time.time() < deadline:
        time.sleep(5)
        traces = client.get(
            f"{ZIPKIN}/api/v2/traces", params={"serviceName": "gateway", "limit": 30}
        ).json()
        for trace in traces:
            services = {
                span.get("localEndpoint", {}).get("serviceName", "") for span in trace
            } | {span.get("remoteEndpoint", {}).get("serviceName", "") for span in trace}
            services.discard("")
            wanted = {"gateway", "intake", "orchestrator", "ai-decision-app"}
            if wanted <= services:
                print(f"TRACE OK: {trace[0]['traceId']} spans={len(trace)}")
                print(f"  services stitched: {sorted(services)}")
                agent_spans = [s["name"] for s in trace if "agent" in s.get("name", "").lower()]
                print(f"  agent spans: {agent_spans}")
                break
            if len(services) > best[0]:
                best = (len(services), services)
        else:
            continue
        break
    else:
        print(f"FAIL: no single trace stitched all services (best so far: {sorted(best[1])})")
        return 1

    up = client.get(f"{PROMETHEUS}/api/v1/query", params={"query": "up"}).json()
    scraped = sum(1 for r in up.get("data", {}).get("result", []) if r["value"][1] == "1")
    print(f"PROMETHEUS OK: {scraped} sidecar targets up")
    if scraped < 5:
        print("FAIL: expected at least 5 scraped sidecars")
        return 1

    print("N4 OBSERVABILITY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
