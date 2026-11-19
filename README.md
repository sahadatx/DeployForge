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

The local dashboard mounts `frontend/src` read-only, so frontend edits appear immediately through Vite. Rebuild the dashboard after changing its dependencies or Dockerfile with `docker compose up -d --no-deps --build dashboard`. The production overlay removes this development mount and serves the built dashboard.

The default Compose setup uses SQLite in a named volume. Alembic migrations run before the API starts. The worker writes a heartbeat and processes durable deployment jobs; the Overview page reads component status from the API. No workspace, user, deployment, or service records are seeded. Three built-in Golden Paths are installed as platform configuration, not example service records.

If you do not have an app or cluster yet, start with the [first local demo](docs/operations/first-demo.md). It runs a small example service separately from DeployForge and does not create fake deployment records.

The demo also includes a repository-owned [CI, supply-chain, and staging GitOps release path](docs/operations/demo-release-path.md): build to GHCR by digest, scan, generate an SBOM, sign and attest, send an authenticated result callback, and prepare a restricted Kubernetes staging overlay. The workflow and manifests are local project files; they do not publish themselves or connect to GitHub/Argo CD until those providers are configured.

Local Compose enables email/password sign-in for operator-provisioned development accounts. The first local account becomes the platform administrator only while first-user bootstrap is enabled; use that account to create a workspace in **Settings**. Development authentication is rejected outside local development environments. A local password does not verify ownership of an email address, so these accounts cannot approve staging operations when verified approval identity is required. Production configuration requires PostgreSQL through the psycopg driver, a complete OIDC client with an explicit HTTPS callback, an HTTPS dashboard origin, a strong session secret, secure cookies, development authentication disabled, and verified OIDC identities for human approvals. PostgreSQL URLs using the standard `postgresql://` scheme are normalized to `postgresql+psycopg://`. A verified email in `DEPLOYFORGE_OIDC_ADMIN_EMAILS` can bootstrap a platform administrator after OIDC sign-in. Locally, the OIDC callback defaults to `http://localhost:8000/api/v1/auth/callback` and should be registered with the identity provider.

### Local account password setup

After starting the API, set the password for an existing local account using its original email address:

```sh
docker compose exec api python -m app.local_accounts set-password --email you@company.com
```

The command prompts for a password and confirmation without displaying either. Use 12–128 characters. To create a new account, append `--create`; `--display-name "Your Name"` optionally sets its display name. Provisioning requires local server access and is unavailable through anonymous HTTP requests. For a directly running API, run the same `python -m app.local_accounts ...` command from `backend` with its virtual environment and database configuration.

Existing accounts retain their IDs, roles and workspace memberships. They need an initial password before using the new sign-in form. The migration revokes sessions created by the old email-only development login; password changes also revoke the affected user's sessions. Passwords are stored as salted scrypt hashes and are never returned by the API. Organization accounts continue to use SSO.

### Seed a local demo workspace

To populate the dashboard tables with connected example records, seed a separate demo workspace for an existing local account:

```sh
docker compose exec api python -m app.demo_data seed --email you@company.com
```

The Northstar Commerce Demo dataset includes 100+ services, releases, builds, GitOps runs, infrastructure runs, policy decisions, audit events, telemetry sources, SLOs, alerts, incidents and policy exceptions, plus 50 workspace-member rows. Demo-only member identities use a reserved example domain, have no password, and are inactive. The selected local account receives platform-engineer access in this demo workspace so it can create and manage operational records; the role change is audit logged. Provider targets are disabled, sample records are marked as synthetic, and rerunning the command fills missing demo rows without duplicating existing ones. Other workspaces and roles are left unchanged. This command is available only in local development with development authentication enabled.

## Run with PostgreSQL

```sh
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build
```

The override adds a PostgreSQL service and points the API and worker at it. Set `POSTGRES_PASSWORD` to a URL-safe random value first (for example, generate a 64-character hexadecimal secret). The override refuses to start with its password unset. Production deployments should provide a managed PostgreSQL URL through `DEPLOYFORGE_DATABASE_URL` and their platform's secret manager.

## Staging and production-style Compose

The production-style Compose overlay is a single-host staging baseline. It runs the Vite-built dashboard on an unprivileged Nginx image, keeps API and PostgreSQL off published host ports, places PostgreSQL on an internal network, mounts secrets only into the API or worker that needs them, and enforces OIDC, secure cookies, PostgreSQL, and verified supply-chain evidence. TLS must terminate at a trusted reverse proxy in front of `127.0.0.1:8080`; the proxy must set `X-Forwarded-Proto` to `https` and register the exact OIDC callback URL. Use Docker Compose 2.24.4 or newer for the override tags.

See [the staging operations runbook](docs/operations/staging-runbook.md) for first setup, CI and GitOps wiring, backup/restore, failure recovery, and the limits of this single-host deployment. `scripts/init-production-secrets.sh` creates local secret files; do not commit `secrets/` or `.env.production`. Production-scale deployments should replace the bundled PostgreSQL service with managed PostgreSQL that provides high availability and point-in-time recovery.

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
- `POST /api/v1/auth/password-login` — development-only email/password login for a provisioned local account
- `POST /api/v1/auth/dev-login` — legacy alias; also requires a valid password
- `GET /api/v1/auth/me` — current user, memberships and CSRF token
- `POST /api/v1/auth/logout` — revoke the server-side session
- `GET /api/v1/workspaces` — only workspaces available to the current user
- `POST /api/v1/workspaces` — create workspace (platform administrator)
- `GET /api/v1/workspaces/{workspace_id}/environments` — membership-protected environment selector data
- `POST /api/v1/workspaces/{workspace_id}/environments` — create a workspace environment
- `PATCH /api/v1/workspaces/{workspace_id}/environments/{environment_id}` — update protection, cluster/namespace binding, and quotas
- `POST/DELETE /api/v1/workspaces/{workspace_id}/environments/{environment_id}/deployment-freeze` — schedule or cancel an audited freeze window
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
- `GET /api/v1/workspaces/{workspace_id}/repository-provisionings` — read recent repository bootstrap jobs
- `POST /api/v1/workspaces/{workspace_id}/services/{service_id}/repository-provisioning` — queue a GitHub repository from the configured Golden Path template
- `POST /api/v1/workspaces/{workspace_id}/repository-provisionings/{provisioning_id}/retry` — retry a blocked bootstrap job

Catalog data is visible to workspace members. Workspace administrators and platform engineers can manage teams and services; changes are written to Audit Logs. Golden Path creation stores the selected template version and validated configuration with the service. When GitHub repository templates are configured, service owners can request private repository creation from the service detail drawer. A durable worker handles GitHub API requests, verifies the returned repository and source template, retries transient failures, records audit events, and updates the service catalog. Repository creation does not start a build or deploy a workload. Configure it using the [GitHub repository integration guide](docs/integrations/github-repositories.md).

The dashboard includes all 11 navigation routes and gates them behind authentication. The signed-in identity, workspace context, platform health, and navigation badges are read from live APIs. Settings supports workspace creation, member role management, and local interface preferences; Audit Logs displays stored identity, authorization, and workspace events; Service Catalog provides live services, ownership teams, lifecycle filters, service details, and Golden Path registration. Deployments, GitOps, Environments, Infrastructure, Policies, Observability, and Incidents use their corresponding workspace-scoped APIs. Actions remain governed by backend authorization checks, even when the UI presents a read-only state. Shared loading, empty, error, and access-denied states are used across the pages.

## Deployment control plane

- `GET/POST /api/v1/workspaces/{workspace_id}/environments` — list environments or create one as a workspace administrator/platform engineer
- `GET/POST /api/v1/workspaces/{workspace_id}/deployments` — list durable release requests or enqueue a request with an optional `Idempotency-Key`
- `GET /api/v1/workspaces/{workspace_id}/promotion-artifacts` — list container artifacts from healthy releases in another environment
- `GET /api/v1/workspaces/{workspace_id}/deployments/{deployment_id}` — read deployment state and recovery details
- `GET /api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/events` — read the ordered, append-only transition history
- `POST /api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/approval` — approve or reject as a different authorized platform operator
- `POST /api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/retry` — retry a failed request within its bounded attempt limit
- `POST /api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/cancel` — cancel while it is still waiting for execution

The API persists a request and returns without running a build/deploy pipeline inside the HTTP call. A worker claims queued jobs under a lease, validates the service and environment, records every state change and audit event, and sends non-production requests to human approval. Production requests fail closed until a policy evaluation provider is configured. Approval creates a durable CI build run; the worker dispatches the selected repository-owned GitHub Actions, GitLab CI, or Azure DevOps pipeline and waits for its authenticated result. A clean build becomes `ready_to_deploy` and is handed to the GitOps reconciler when an Argo CD or Flux target is registered. Operators can also promote a container artifact from a healthy deployment in another environment; the release copies its artifact evidence without rebuilding, then still passes target approval, policy evaluation and GitOps reconciliation for an explicitly selected manifest commit.

### CI/CD and supply-chain evidence

- `GET /api/v1/workspaces/{workspace_id}/build-runs` — list durable build attempts, optionally filtered by deployment and status
- `GET /api/v1/workspaces/{workspace_id}/build-runs/{build_run_id}` — inspect ordered build events and reported artifact evidence
- `POST /api/v1/workspaces/{workspace_id}/build-runs/{build_run_id}/retry` — requeue a blocked run after its provider configuration is fixed
- `POST /api/v1/build-runs/{build_run_id}/callback` — receive a timestamped, HMAC-authenticated workflow update; request deliveries are replay-protected

Set `DEPLOYFORGE_BUILD_PROVIDER` to `github_actions` (default), `gitlab_ci`, or `azure_devops`, and configure its worker-only credential plus the runner-reachable HTTPS `DEPLOYFORGE_BUILD_CALLBACK_PUBLIC_URL`. Configure `DEPLOYFORGE_BUILD_CALLBACK_SECRET` (at least 32 random characters) for the API and the matching CI secret in the service repository. GitHub tokens should have Actions write permission only for allowed repositories. For GitLab, configure its server/API URLs and CI config path; its project-scoped API token is mounted into the worker only. For Azure DevOps, configure its organization URL, organization GUID OIDC issuer, and explicit Project/Repository-to-pipeline map; the Azure build token is mounted into the worker only. Azure production verification requires a private Sigstore trust root. See the [GitHub Actions](docs/integrations/github-actions.md), [GitLab CI](docs/integrations/gitlab-ci.md), and [Azure DevOps](docs/integrations/azure-devops.md) guides. GitHub Enterprise Server uses `DEPLOYFORGE_GITHUB_SERVER_URL` and `DEPLOYFORGE_GITHUB_API_URL`. `DEPLOYFORGE_INFRASTRUCTURE_WORKFLOW_PATH` continues to select the Terraform/OpenTofu workflow, which currently runs on GitHub Actions. Copy provider variables from `.env.example` into a local `.env` for development; use a secret manager in production.

Each successful callback reports artifact, SBOM, vulnerability, signature, and provenance evidence. Development stores these as `workflow_reported`. Production independently verifies the artifact signature, SLSA provenance, signed SBOM contents, and digest-bound Grype summary against the configured Sigstore trust root before the release can advance. Provider-specific identity rules and the shared evidence contract are documented in the [GitHub Actions](docs/integrations/github-actions.md), [GitLab CI](docs/integrations/gitlab-ci.md), and [Azure DevOps](docs/integrations/azure-devops.md) guides.

The selected CI adapter reports dispatch acceptance, not completion. Workflow progress/final status arrives through signed callbacks. Missing provider configuration leaves a run visibly `blocked`; dispatch errors, callback timeout, workflow failures, and vulnerability threshold breaches are persisted with build/deployment events and audit entries. The workflow itself and build commands stay repository-owned because language, registry, SBOM generator, scanner, and signing policy differ by service.

See the [GitHub Actions](docs/integrations/github-actions.md), [GitLab CI](docs/integrations/gitlab-ci.md), or [Azure DevOps](docs/integrations/azure-devops.md) build guide for provider setup, identity policy, and the shared signed callback contract.

### Kubernetes and GitOps reconciliation

- `GET/POST /api/v1/workspaces/{workspace_id}/gitops-targets` — list or register an Argo CD Application or Flux Kustomization binding for a service/environment pair
- `PATCH /api/v1/workspaces/{workspace_id}/gitops-targets/{target_id}` — update or disable a binding
- `GET /api/v1/workspaces/{workspace_id}/gitops-targets/{target_id}/status` — inspect provider connectivity and live Application state
- `GET /api/v1/workspaces/{workspace_id}/gitops-runs` — list durable rollout and rollback attempts
- `GET /api/v1/workspaces/{workspace_id}/gitops-runs/{gitops_run_id}` — read desired/observed revisions, rollout/AnalysisRun evidence, action history, and ordered events
- `POST /api/v1/workspaces/{workspace_id}/gitops-runs/{gitops_run_id}/promote` and `/abort` — queue audited stage actions with an operator reason
- `POST /api/v1/workspaces/{workspace_id}/gitops-runs/{gitops_run_id}/retry` — retry a blocked run after correcting target/provider configuration
- `POST /api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/rollback` — queue an audited rollback to the prior healthy target revision

Configure `DEPLOYFORGE_ARGOCD_API_URL` and `DEPLOYFORGE_ARGOCD_AUTH_TOKEN` for the worker only. The API URL must use HTTPS; keep the Argo CD token scoped and in a secret manager in production. Configure `DEPLOYFORGE_ARGO_ROLLOUTS_API_URL` for the API and worker to enable stage evidence and actions; keep `DEPLOYFORGE_ARGO_ROLLOUTS_AUTH_TOKEN` in the worker secret only. Register one single-source Application target per service/environment. The service repository, expected Argo CD project, and optional cluster name are checked before sync. DeployForge keeps the built source commit separate from the image-digest-pinned GitOps manifest commit, requests a sync for that exact manifest revision, disables pruning, and only reports deployment success after Argo CD observes the exact revision as `Synced` and `Healthy`. In verified supply-chain mode, the live Argo CD image summary must also show the same OCI repository and artifact digest. A timed-out, degraded, or digest-mismatched rollout is failed and automatically returned to the prior healthy revision when one exists; operators can also request a manual rollback for the latest successful target deployment. Every target change, operator action, transition, and recovery attempt is recorded in the audit history.

See [Argo CD GitOps integration](docs/integrations/argocd.md) for target registration, API examples, supported behavior, and recovery steps.

Flux uses a worker-only Kubernetes API token, a commit-pinned GitRepository, and a Kustomization with readiness checks. Verified OCI releases also require a target-specific workload label before digest evidence is accepted. See the [Flux integration guide](docs/integrations/flux.md) for the minimum API permissions, configuration, and supported boundaries.

### Environments and infrastructure plans

Environments store cluster/namespace bindings, protection state, destroy controls, optional CPU, memory, pod, PVC, and Service quotas, plus audited deployment freeze windows. Each environment can have one Terraform/OpenTofu target with a canonical GitHub repository, source ref, working directory, CLI selection, and optional remote backend workspace. Durable plan/drift runs are dispatched to a repository-owned GitHub Actions workflow; signed callbacks return saved-plan digests, resource-change counts, quota usage, and workflow status. Changed plans require approval by a different platform operator, and apply must use the exact saved-plan digest that was reviewed. Production environments default to protected; destructive plans fail closed unless a platform administrator enables them. Drift checks report `drifted` without applying changes, and an environment-level lock prevents overlapping plan/apply runs.

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

### Incident response

The Incidents page maintains workspace-scoped records for manual intake and active SLO, failed deployment, or blocked/failed GitOps signals. Signal conversion is idempotent per source record. Responders can assign workspace members, record escalation levels, progress through investigation/mitigation/resolution/closure, and attach an append-only response timeline; each timeline change also writes to Audit Logs. PagerDuty receives durable Events API trigger/resolve notifications when its routing key is configured on the worker. External on-call schedules and paging escalation policies remain owned by PagerDuty. Resolved incidents support structured postmortem drafts with independent approval before publication. See the [incident response runbook](docs/operations/incident-response.md).

## Isolated APK parser

The parser runs on an internal-only Compose network, has no published port, runs as an unprivileged user, and has a read-only root filesystem. Its health endpoint is available to Compose. Workspace operators can stream an APK to the authenticated API for bounded package metadata, permissions, SDK, signing-certificate fingerprint, and hash inspection. The parser never installs or executes the uploaded APK. See the [APK inspection guide](docs/integrations/apk-inspection.md).

## Known integration boundaries

- Production evidence verification validates the artifact signature, CI identity, SLSA provenance, signed SBOM contents, and a digest-bound Grype summary. It supports GitHub Actions, GitLab CI, and Azure DevOps with a checksum-pinned private Sigstore trust root. GitOps releases support Argo CD and Flux. Flux uses a dedicated GitRepository that the worker pins to the exact release commit, plus target-specific Pod labels for verified OCI digest inspection; see the [Flux integration guide](docs/integrations/flux.md).
- Incident records, ownership, escalation history, transitions, append-only response timelines, and reviewed postmortems are implemented. PagerDuty owns on-call schedules, paging escalation policies, and incident conference management; DeployForge records the application-side response workflow and relays trigger/resolve events.
- DeployForge exports telemetry to an external OpenTelemetry Collector but does not provide telemetry storage or a charting backend.
- Golden Path registration saves validated service metadata and can create a private GitHub repository from configured templates, then installs the shared build callback secret and URL. Template contents, provider-specific workflow configuration, and registry credentials remain owned by the platform team.
- Progressive delivery supports an Argo Rollouts canary with persisted, bounded AnalysisRun evidence and audited stepwise promote/abort controls. Automatic analysis, traffic routing, and stage progression remain owned by Argo Rollouts. See the [progressive delivery guide](docs/operations/progressive-delivery.md).

## Checks

```sh
(cd backend && python -m pip install -r requirements-dev.txt && ruff check app tests && pytest)
(cd frontend && npm run typecheck && npm run build)
(cd frontend && npx playwright install chromium && npm run test:e2e)
docker compose config
```

Pull requests and pushes run backend dependency audits, style checks and tests, apply and verify migrations against PostgreSQL, and typecheck/build/audit the frontend plus Chromium end-to-end approval checks in [DeployForge CI](.github/workflows/ci.yml). Third-party actions are pinned to full commit SHAs, with weekly Dependabot updates for GitHub Actions, Python, and npm packages.

