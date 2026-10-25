# DeployForge Enterprise

DeployForge is a workspace-scoped DevOps and platform engineering control plane for service ownership, release orchestration, GitOps, infrastructure change control, policy enforcement, and operational reliability.

## Stack

- Dashboard: React 19, TypeScript, Vite 8, React Router, custom CSS and Lucide React
- API and worker: Python 3.11+, FastAPI, Pydantic Settings, Uvicorn
- Data: SQLAlchemy 2, Alembic, SQLite for local development and PostgreSQL for production-style deployments
- Local orchestration: Docker Compose

## Run locally with Docker Compose

```sh
docker compose up --build
```

Open the dashboard at `http://localhost:5173`, the API docs at `http://localhost:8000/docs`, and the readiness endpoint at `http://localhost:8000/api/v1/health/ready`.

The default Compose setup uses SQLite in a named volume. Alembic migrations run before the API starts. The worker writes a heartbeat and processes durable deployment jobs; the Overview page reads component status from the API. No workspace, user, deployment, or service records are seeded. Three built-in Golden Paths are installed as platform configuration, not example service records.

If you do not have an app or cluster yet, start with the [first local demo](docs/operations/first-demo.md). It runs a small example service separately from DeployForge and does not create fake deployment records.

The demo also includes a repository-owned [CI, supply-chain, and staging GitOps release path](docs/operations/demo-release-path.md): build to GHCR by digest, scan, generate an SBOM, sign and attest, send an authenticated result callback, and prepare a restricted Kubernetes staging overlay. The workflow and manifests are local project files; they do not publish themselves or connect to GitHub/Argo CD until those providers are configured.

Local Compose enables a clearly labeled development-only sign-in. The first local developer account becomes the platform administrator only while first-user bootstrap is enabled; use that account to create a workspace in **Settings**. Development authentication is rejected outside local development environments. Production configuration requires PostgreSQL through the psycopg driver, a complete OIDC client with an explicit HTTPS callback, an HTTPS dashboard origin, a strong session secret, secure cookies, and development authentication disabled. PostgreSQL URLs using the standard `postgresql://` scheme are normalized to `postgresql+psycopg://`. A verified email in `DEPLOYFORGE_OIDC_ADMIN_EMAILS` can bootstrap a platform administrator after OIDC sign-in. Locally, the OIDC callback defaults to `http://localhost:8000/api/v1/auth/callback` and should be registered with the identity provider.

## Run with PostgreSQL

```sh
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build
```

The override adds a PostgreSQL service and points the API and worker at it. Set `POSTGRES_PASSWORD` to a URL-safe random value first (for example, generate a 64-character hexadecimal secret). The override refuses to start with its password unset. Production deployments should provide a managed PostgreSQL URL through `DEPLOYFORGE_DATABASE_URL` and their platform's secret manager.

## Staging and production-style Compose

The production-style Compose overlay is a single-host staging baseline. It runs the Vite-built dashboard on an unprivileged Nginx image, keeps API and PostgreSQL off published host ports, places PostgreSQL on an internal network, mounts secrets only into the API or worker that needs them, and enforces OIDC, secure cookies, PostgreSQL, and verified supply-chain evidence. TLS must terminate at a trusted reverse proxy in front of `127.0.0.1:8080`; the proxy must set `X-Forwarded-Proto` to `https` and register the exact OIDC callback URL. Use Docker Compose 2.24.4 or newer for the override tags.

See [the staging operations runbook](docs/operations/staging-runbook.md) for first setup, GitHub Actions and Argo CD wiring, backup/restore, failure recovery, and the limits of this single-host deployment. `scripts/init-production-secrets.sh` creates local secret files; do not commit `secrets/` or `.env.production`. Production-scale deployments should replace the bundled PostgreSQL service with managed PostgreSQL that provides high availability and point-in-time recovery.

## Run services directly

API and worker:

```sh
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

In another terminal, start the worker:

```sh
cd backend
. .venv/bin/activate
python -m app.worker
```

Dashboard:

```sh
cd frontend
npm ci
npm run dev
```

The Vite server proxies `/api/v1` to `http://localhost:8000` by default. Override `VITE_BACKEND_ORIGIN` when the API runs elsewhere.

## Identity and workspace access API

- `GET /api/v1/health/live` — process liveness
- `GET /api/v1/health/ready` — database readiness
- `GET /api/v1/system/status` — authenticated API, database and worker heartbeat status
- `GET /api/v1/auth/config` — enabled sign-in methods (never exposes credentials)
- `GET /api/v1/auth/login` and `GET /api/v1/auth/callback` — OIDC Authorization Code + PKCE login
- `POST /api/v1/auth/dev-login` — development-only local identity
- `GET /api/v1/auth/me` — current user, memberships and CSRF token
- `POST /api/v1/auth/logout` — revoke the server-side session
- `GET /api/v1/workspaces` — only workspaces available to the current user
- `POST /api/v1/workspaces` — create workspace (platform administrator)
- `GET /api/v1/workspaces/{workspace_id}/environments` — membership-protected environment selector data
- `POST /api/v1/workspaces/{workspace_id}/environments` — create a workspace environment
- `PATCH /api/v1/workspaces/{workspace_id}/environments/{environment_id}` — update protection, cluster/namespace binding, and quotas
- `GET/POST /api/v1/workspaces/{workspace_id}/members` — view or grant workspace access
- `PATCH/DELETE /api/v1/workspaces/{workspace_id}/members/{user_id}` — change a role or revoke access
- `GET /api/v1/audit-logs` — read platform or workspace-scoped audit history

Browser sessions use opaque HttpOnly cookies; only a hash of the session token is stored in the database. State-changing endpoints require the session's CSRF token. Workspace roles are `workspace_admin`, `platform_engineer`, `service_owner`, and `auditor`; platform administrator is a separately bootstrapped global permission. Membership changes and workspace creation write audit events. Identity providers are configured through deployment secrets rather than the dashboard.

## Service catalog and Golden Paths

- `GET /api/v1/workspaces/{workspace_id}/services` — workspace-scoped, searchable and filterable catalog with pagination
- `GET /api/v1/workspaces/{workspace_id}/services/summary` — total, production, experimental and ownership-team counts
- `POST /api/v1/workspaces/{workspace_id}/services` — register a service with an owner team and catalog metadata
- `GET/PATCH /api/v1/workspaces/{workspace_id}/services/{service_id}` — read or update service metadata
- `POST /api/v1/workspaces/{workspace_id}/services/{service_id}/archive` — retire a service while preserving its history
- `GET/POST /api/v1/workspaces/{workspace_id}/teams` — list or create workspace-scoped ownership teams
- `GET /api/v1/workspaces/{workspace_id}/golden-paths` — list available platform templates
- `POST /api/v1/workspaces/{workspace_id}/golden-paths/{slug}/instantiate` — register service metadata from a validated Golden Path

Catalog data is visible to workspace members. Workspace administrators and platform engineers can manage teams and services; changes are written to Audit Logs. Golden Path creation stores the selected template version and validated configuration with the service. It does not create a remote repository, run a build, or deploy a workload; repository scaffolding remains an integration boundary.

The dashboard includes all 11 navigation routes and gates them behind authentication. The signed-in identity, workspace context, platform health, and navigation badges are read from live APIs. Settings supports workspace creation, member role management, and local interface preferences; Audit Logs displays stored identity, authorization, and workspace events; Service Catalog provides live services, ownership teams, lifecycle filters, service details, and Golden Path registration. Deployments, GitOps, Environments, Infrastructure, Policies, Observability, and Incidents use their corresponding workspace-scoped APIs. Actions remain governed by backend authorization checks, even when the UI presents a read-only state. Shared loading, empty, error, and access-denied states are used across the pages.

## Deployment control plane

- `GET/POST /api/v1/workspaces/{workspace_id}/environments` — list environments or create one as a workspace administrator/platform engineer
- `GET/POST /api/v1/workspaces/{workspace_id}/deployments` — list durable release requests or enqueue a request with an optional `Idempotency-Key`
- `GET /api/v1/workspaces/{workspace_id}/deployments/{deployment_id}` — read deployment state and recovery details
- `GET /api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/events` — read the ordered, append-only transition history
- `POST /api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/approval` — approve or reject as a different authorized platform operator
- `POST /api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/retry` — retry a failed request within its bounded attempt limit
- `POST /api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/cancel` — cancel while it is still waiting for execution

The API persists a request and returns without running a build/deploy pipeline inside the HTTP call. A worker claims queued jobs under a lease, validates the service and environment, records every state change and audit event, and sends non-production requests to human approval. Production requests fail closed until a policy evaluation provider is configured. Approval creates a durable CI build run; the worker dispatches a repository-owned GitHub Actions workflow and waits for its authenticated result. A clean build becomes `ready_to_deploy` and is handed to the GitOps reconciler when an Argo CD target is registered.

### CI/CD and supply-chain evidence

- `GET /api/v1/workspaces/{workspace_id}/build-runs` — list durable build attempts, optionally filtered by deployment and status
- `GET /api/v1/workspaces/{workspace_id}/build-runs/{build_run_id}` — inspect ordered build events and reported artifact evidence
- `POST /api/v1/workspaces/{workspace_id}/build-runs/{build_run_id}/retry` — requeue a blocked run after its provider configuration is fixed
- `POST /api/v1/build-runs/{build_run_id}/callback` — receive a timestamped, HMAC-authenticated workflow update; request deliveries are replay-protected

Configure the repository-scoped `DEPLOYFORGE_GITHUB_ACTIONS_TOKEN` and runner-reachable HTTPS `DEPLOYFORGE_BUILD_CALLBACK_PUBLIC_URL` for the worker. Configure `DEPLOYFORGE_BUILD_CALLBACK_SECRET` (at least 32 random characters) for the API; put the same callback secret in the service repository's GitHub Actions secrets. The GitHub credential should be restricted to only the repositories DeployForge builds and have Actions write permission. `DEPLOYFORGE_GITHUB_ACTIONS_WORKFLOW_PATH` selects the build workflow. `DEPLOYFORGE_INFRASTRUCTURE_WORKFLOW_PATH` selects the Terraform/OpenTofu workflow. Both workflow paths must point to YAML files directly inside `.github/workflows`. Copy the provider variables from `.env.example` into a local `.env` for development; use a secret manager in production.

Each successful callback reports artifact, SBOM, vulnerability, signature, and provenance evidence. Development stores these as `workflow_reported`. Production requires attached Sigstore and SLSA bundles, verifies the artifact digest and workflow identity against Sigstore's production trust root, and verifies the SLSA statement's subject digest and source commit before the release can advance. See the [GitHub Actions build integration](docs/integrations/github-actions.md) for the signed-evidence contract.

The GitHub Actions adapter reports dispatch acceptance, not completion. Workflow progress/final status arrives through signed callbacks. Missing provider configuration leaves a run visibly `blocked`; GitHub dispatch errors, callback timeout, workflow failures, and vulnerability threshold breaches are persisted with build/deployment events and audit entries. The workflow itself and build commands stay repository-owned because language, registry, SBOM generator, scanner, and signing policy differ by service.

See [GitHub Actions build integration](docs/integrations/github-actions.md) for the workflow input and signed callback contract.

### Kubernetes and GitOps reconciliation

- `GET/POST /api/v1/workspaces/{workspace_id}/gitops-targets` — list or register an Argo CD Application binding for a service/environment pair
- `PATCH /api/v1/workspaces/{workspace_id}/gitops-targets/{target_id}` — update or disable a binding
- `GET /api/v1/workspaces/{workspace_id}/gitops-targets/{target_id}/status` — inspect provider connectivity and live Application state
- `GET /api/v1/workspaces/{workspace_id}/gitops-runs` — list durable rollout and rollback attempts
- `GET /api/v1/workspaces/{workspace_id}/gitops-runs/{gitops_run_id}` — read desired/observed revision and ordered reconciliation events
- `POST /api/v1/workspaces/{workspace_id}/gitops-runs/{gitops_run_id}/retry` — retry a blocked run after correcting target/provider configuration
- `POST /api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/rollback` — queue an audited rollback to the prior healthy target revision

Configure `DEPLOYFORGE_ARGOCD_API_URL` and `DEPLOYFORGE_ARGOCD_AUTH_TOKEN` for the worker only. The API URL must use HTTPS; keep the Argo CD token scoped and in a secret manager in production. Register one single-source Application target per service/environment. The service repository, expected Argo CD project, and optional cluster name are checked before sync. DeployForge keeps the built source commit separate from the image-digest-pinned GitOps manifest commit, requests a sync for that exact manifest revision, disables pruning, and only reports deployment success after Argo CD observes the exact revision as `Synced` and `Healthy`. A timed-out or degraded rollout is failed and automatically returned to the prior healthy revision when one exists; operators can also request a manual rollback for the latest successful target deployment. Every target change, operator action, transition, and recovery attempt is recorded in the audit history.

See [Argo CD GitOps integration](docs/integrations/argocd.md) for target registration, API examples, supported behavior, and recovery steps.

### Environments and infrastructure plans

Environments store cluster/namespace bindings, protection state, destroy controls, and optional CPU, memory, pod, PVC, and Service quotas. Each environment can have one Terraform/OpenTofu target with a canonical GitHub repository, source ref, working directory, CLI selection, and optional remote backend workspace. Durable plan/drift runs are dispatched to a repository-owned GitHub Actions workflow; signed callbacks return saved-plan digests, resource-change counts, quota usage, and workflow status. Changed plans require approval by a different platform operator, and apply must use the exact saved-plan digest that was reviewed. Production environments default to protected; destructive plans fail closed unless a platform administrator enables them. Drift checks report `drifted` without applying changes, and an environment-level lock prevents overlapping plan/apply runs.

See [Terraform and OpenTofu infrastructure runs](docs/integrations/terraform-opentofu.md) for the workflow callback contract, target registration, quota units, and approval/recovery flow.

### Policy-as-Code

Workspace policies use a versioned, bounded JSON rule format with enforce/monitor modes, immutable evaluation snapshots, policy-gated production releases, artifact and infrastructure checks, and independently approved, expiring exceptions. Production deployments fail closed until an enabled enforced deployment policy has a blocking or approval rule. Artifact rules are evaluated after CI verifies the built artifact and before GitOps; rules requiring another approval pause the release at that point. The built-in environment quota and protected-destroy controls remain mandatory.

See [Policy-as-Code](docs/policies.md) for the rule schema, supported fields/operators, exception lifecycle, and API routes.

### Observability and SRE

- `POST /api/v1/workspaces/{workspace_id}/telemetry-sources` — create a service/environment-bound metrics credential; the token is returned once, stored as a hash, and can be revoked with `PATCH /api/v1/workspaces/{workspace_id}/telemetry-sources/{source_id}`.
- `POST /api/v1/telemetry/samples` — ingest bounded, idempotent interval aggregates using the source's bearer token. Samples contain request/error totals and optional cumulative latency buckets at 100, 300, 500, 1,000, 2,500, and 5,000 ms.
- `POST /api/v1/workspaces/{workspace_id}/slos` — define availability or latency objectives for a service, optionally scoped to an environment, with a rolling window.
- `POST /api/v1/workspaces/{workspace_id}/slos/{slo_id}/evaluate` and `GET /api/v1/workspaces/{workspace_id}/slos/{slo_id}/evaluations` — evaluate persisted samples and inspect attainment, error budget, burn rate, and history. The worker evaluates enabled objectives at least once per minute.
- `GET /api/v1/workspaces/{workspace_id}/alerts` and `POST /api/v1/workspaces/{workspace_id}/alerts/{alert_id}/acknowledge` — inspect firing/acknowledged/resolved SLO alerts. A breach opens one alert; subsequent evaluations do not duplicate it, and healthy telemetry resolves it automatically.

Source creation, credential revocation, SLO definitions, and alert transitions are written to Audit Logs. Telemetry is workspace- and service-scoped, request batches are limited to 250 samples, timestamps are bounded, and raw source tokens are never returned from list APIs. Samples older than `DEPLOYFORGE_TELEMETRY_RETENTION_DAYS` (7–365 days; default 31) are pruned by the worker.

Set `OTEL_EXPORTER_OTLP_ENDPOINT` to a trusted OpenTelemetry Collector's OTLP/HTTP endpoint to export traces and metrics, or set the traces and metrics endpoint variables independently to enable only the requested signal. Without an endpoint, export stays disabled. Application logs remain structured JSON on stdout; when tracing is enabled, request logs include trace and span IDs for correlation with the Collector backend. DeployForge does not include a metrics/logs/traces storage or visualization backend; the Observability page manages telemetry credentials, SLO objectives, evaluations, and alerts while charting/storage are provided by connected telemetry systems.

## Isolated APK parser

The parser runs on an internal-only Compose network, has no published port, runs as an unprivileged user, and has a read-only root filesystem. Its health endpoint is available to Compose. The parse endpoint currently returns `501 Not Implemented`; APK analysis is not part of the implemented control plane workflows.

## Known integration boundaries

- Production evidence verification validates the signature, workflow identity, SLSA statement, artifact digest, and source commit. SBOM content and vulnerability counts are still supplied by the repository workflow; pin and configure its generator and scanner.
- The Incidents page consolidates firing SLO alerts and failed or blocked release/GitOps runs. A standalone incident record, on-call schedule, and paging integration are not implemented.
- DeployForge exports telemetry to an external OpenTelemetry Collector but does not provide telemetry storage or a charting backend.
- Golden Path registration saves validated service metadata; remote repository creation and onboarding automation remain integration work.
- The isolated APK parser endpoint is a hardened placeholder and does not parse APK files yet.

## Checks

```sh
cd backend && python -m pip install -r requirements-dev.txt && ruff check app tests && pytest
cd frontend && npm run typecheck && npm run build
docker compose config
```

Pull requests and pushes run backend dependency audits, style checks and tests, apply and verify migrations against PostgreSQL, and typecheck/build/audit the frontend in [DeployForge CI](.github/workflows/ci.yml). Dependency update pull requests are scheduled weekly for GitHub Actions, Python, and npm packages.

