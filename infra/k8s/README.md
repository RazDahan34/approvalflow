# Deploying ApprovalFlow to a local Kubernetes cluster (B3)

The manifests mirror the proven docker-compose stack, with the k8s-idiomatic swaps:
sidecars are **injected by annotation**, name resolution is **kubernetes-native**
(no sqlite registry), secrets live in **Kubernetes Secrets**, and the policy document
ships as a **ConfigMap generated from the repo's `policy.md`**.

## Prerequisites

- A local cluster: Docker Desktop → Settings → Kubernetes → Enable (or `kind create cluster`).
- The Dapr control plane (operator, sidecar injector, placement, scheduler, sentry):

  ```bash
  dapr init -k --wait
  ```

- Images are published to GHCR by the CD pipeline on every green `main`. For a private
  repo the cluster needs a pull secret (a GitHub PAT with `read:packages`):

  ```bash
  kubectl create namespace approvalflow
  kubectl -n approvalflow create secret docker-registry ghcr-creds \
    --docker-server=ghcr.io --docker-username=<github-user> --docker-password=<PAT>
  ```

- Application secrets:

  ```bash
  kubectl -n approvalflow create secret generic approvalflow-secrets \
    --from-literal=jwt-secret='change-me-dev-only-secret-0123456789abcdef' \
    --from-literal=postgres-password='approvalflow' \
    --from-literal=audit-connstring='host=postgres.approvalflow user=approvalflow password=approvalflow port=5432 database=audit' \
    --from-literal=llm-provider='stub'
  ```

## Deploy

```bash
# the policy document, straight from the repo root — single source of truth
kubectl -n approvalflow create configmap policy-doc --from-file=policy.md=policy.md

kubectl apply -k infra/k8s
kubectl -n approvalflow get pods -w    # wait until everything is 2/2 (app + sidecar)
```

## Use it

On Docker Desktop the LoadBalancer services bind localhost directly:

- Gateway → http://localhost:8080 · UI → http://localhost:3000

On kind, use port-forwarding instead:

```bash
kubectl -n approvalflow port-forward svc/gateway 8080:8080 &
kubectl -n approvalflow port-forward svc/ui 3000:3000 &
```

Then the same verification suite runs unchanged against the cluster:

```bash
python verify/run_journeys.py
```

> Note: the M11 restart-resilience step in the suite uses `docker compose` to recreate
> the orchestrator; on k8s demonstrate it manually instead — `kubectl -n approvalflow
> delete pod -l app=orchestrator` while an item waits for approval, then approve it and
> watch the workflow resume.
