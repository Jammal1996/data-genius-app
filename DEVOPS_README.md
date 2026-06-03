# DataGenius — DevOps Deployment Guide

A complete 5-stage deployment pipeline for the DataGenius Flask app.

---

## Stage 1 — Containerize

```bash
# Build the image
docker build -t data-genius:latest .

# Run locally
docker run -p 8000:8000 data-genius:latest

# Test it
open http://localhost:8000
```

**Files:** `Dockerfile`, `.dockerignore`

**What's included:**
- Multi-stage build (builder + slim runtime) — keeps image small (~200MB vs ~1GB)
- Non-root `appuser` for security
- Gunicorn with 2 workers + 4 threads for production
- Health check baked in
- Persistent volumes for `static/charts/` and `static/uploads/`

---

## Stage 2 — Deploy to the Cloud

```bash
# Start locally with Docker Compose (simulates cloud environment)
docker compose up -d

# With nginx reverse proxy
docker compose --profile with-nginx up -d
```

**For real cloud deployment:**

| Provider | Command |
|---|---|
| **AWS ECS** | Push to ECR → update ECS service |
| **GCP Cloud Run** | `gcloud run deploy data-genius --image gcr.io/PROJECT/data-genius` |
| **Azure ACI** | `az container create --image ACR_URL/data-genius` |

Push to registry first:
```bash
# GitHub Container Registry (used in CI/CD)
docker tag data-genius:latest ghcr.io/YOUR_ORG/data-genius:latest
docker push ghcr.io/YOUR_ORG/data-genius:latest

# AWS ECR
aws ecr get-login-password | docker login --username AWS --password-stdin ACCOUNT.dkr.ecr.REGION.amazonaws.com
docker tag data-genius:latest ACCOUNT.dkr.ecr.REGION.amazonaws.com/data-genius:latest
docker push ACCOUNT.dkr.ecr.REGION.amazonaws.com/data-genius:latest
```

**Files:** `docker-compose.yml`

---

## Stage 3 — Automate with CI/CD

**File:** `.github/workflows/ci-cd.yml`

Pipeline flow on each `git push`:
```
push → test (pytest) → build image → push to GHCR → deploy
```

**Branch → Environment mapping:**
- `develop` → Development
- `staging` → Staging  
- `main` → Production

**Setup steps:**
1. Add `SECRET_KEY` to GitHub repo secrets (Settings → Secrets → Actions)
2. Uncomment the correct deploy block in `ci-cd.yml` for your cloud provider
3. Push to trigger the pipeline

---

## Stage 4 — Orchestrate with Kubernetes

```bash
# Apply all manifests
kubectl apply -f kubernetes/

# Check status
kubectl get pods -n data-genius
kubectl get hpa -n data-genius

# View logs
kubectl logs -f deployment/data-genius -n data-genius

# Scale manually
kubectl scale deployment/data-genius --replicas=4 -n data-genius
```

**File:** `kubernetes/deployment.yml`

**What's included:**
- `Deployment` with rolling update (zero downtime)
- `Service` (ClusterIP) + `Ingress` with TLS
- `HorizontalPodAutoscaler`: scales 2→10 pods at CPU >70% or memory >80%
- Liveness + readiness probes
- Resource requests/limits (CPU: 250m–1000m, Memory: 512Mi–1Gi)

**Create the required secret:**
```bash
kubectl create secret generic data-genius-secrets \
  --from-literal=SECRET_KEY=your-production-secret-key \
  -n data-genius
```

---

## Stage 5 — Observe in Production

```bash
# Start full observability stack
docker compose \
  -f docker-compose.yml \
  -f observability/docker-compose.observability.yml \
  up -d

# Open dashboards
open http://localhost:3000   # Grafana (admin / admin)
open http://localhost:9090   # Prometheus
```

**Files:** `observability/`

**Stack:**
- **Prometheus** — scrapes `/metrics` every 15s, retains 15 days
- **Grafana** — dashboards for HTTP rate, latency, CPU, memory
- **Loki + Promtail** — log aggregation from all containers
- **Alert rules** — fires on: app down, 5xx rate >5%, P95 latency >5s, CPU >85%, memory >900MB

**One-time app wiring** (see `observability/app_metrics_patch.py`):
```bash
# Add to requirements.txt
echo "prometheus-flask-exporter>=0.23.0" >> requirements.txt

# Add 2 lines to app.py after app = Flask(__name__)
# from prometheus_flask_exporter import PrometheusMetrics
# metrics = PrometheusMetrics(app)
```

---

## File Structure

```
data_genius_app/
├── app.py
├── requirements.txt
├── Dockerfile                          ← Stage 1
├── .dockerignore                       ← Stage 1
├── docker-compose.yml                  ← Stage 2
├── .github/
│   └── workflows/
│       └── ci-cd.yml                   ← Stage 3
├── kubernetes/
│   └── deployment.yml                  ← Stage 4
└── observability/
    ├── docker-compose.observability.yml ← Stage 5
    ├── prometheus.yml                   ← Stage 5
    ├── alerts.yml                       ← Stage 5
    └── app_metrics_patch.py             ← Stage 5
```
