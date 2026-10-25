# Terraform and OpenTofu infrastructure runs

DeployForge models each workspace environment separately from its infrastructure repository. An environment may include a cluster/namespace binding, protection flags, and quota limits. An Infrastructure Target binds that environment to a repository-owned workflow, toolchain, source ref, working directory, and optional remote backend workspace.

The API never executes Terraform or OpenTofu itself. A durable worker dispatches a repository-owned GitHub Actions workflow. The runner uses the selected CLI and the repository's configured remote state backend. Use short-lived cloud credentials through the cloud provider's workload identity/OIDC integration; do not pass cloud credentials as workflow inputs.

## Configure GitHub Actions

Configure the GitHub token and callback URL for the worker, and the callback HMAC secret for the API. The GitHub token should have Actions write access only to the infrastructure repositories that need it. The service repository's workflow must receive the same callback secret from its own GitHub Actions secret store:

```dotenv
DEPLOYFORGE_GITHUB_ACTIONS_TOKEN=<scoped GitHub token>
DEPLOYFORGE_BUILD_CALLBACK_SECRET=<at least 32 random characters>
DEPLOYFORGE_BUILD_CALLBACK_PUBLIC_URL=https://deployforge.example.com
DEPLOYFORGE_INFRASTRUCTURE_WORKFLOW_PATH=.github/workflows/deployforge-infrastructure.yml
DEPLOYFORGE_BUILD_CALLBACK_TIMEOUT_SECONDS=14400
```

The workflow file is owned and reviewed in each infrastructure repository. DeployForge sends `workflow_dispatch` inputs named `infrastructure_run_id`, `environment_id`, `operation`, `run_kind`, `tool`, `source_ref`, `working_directory`, `backend_workspace`, `cluster_name`, `namespace`, `protected`, `allow_destroy`, `quota_limits`, `plan_digest`, `plan_artifact_uri`, and `callback_url`.

For a `plan` operation, the workflow should check out the requested ref, initialize the selected Terraform/OpenTofu backend, produce and save a plan, compute a SHA-256 digest, upload the plan artifact, calculate resource change counts and projected quota usage, then POST a signed `planned` callback. For an `apply` operation, download that exact plan artifact, verify its digest equals the approved digest, and apply the saved plan file. Do not silently generate a fresh plan during the apply operation. Sign callback bodies with HMAC-SHA256 using the configured callback secret and the `X-DeployForge-Timestamp` / `X-DeployForge-Signature` headers, following the [GitHub Actions build callback pattern](github-actions.md).

For `run_kind=drift`, run a read-only refresh plan and report whether changes were found; DeployForge records `drifted` or `succeeded` and never queues an apply for a drift run. OpenTofu supports remote backends with CLI-driven remote runs where the selected backend supports them; the runner workflow remains responsible for its exact backend behavior ([OpenTofu remote backend](https://opentofu.org/docs/v1.6/language/settings/backends/remote/)).

## Register a target and environment

Create an environment with optional cluster binding and enforced quota dimensions:

```http
POST /api/v1/workspaces/{workspace_id}/environments
```

```json
{
  "slug": "staging",
  "name": "Staging",
  "environment_type": "staging",
  "cluster_name": "platform-staging",
  "namespace": "payments-staging",
  "quota_cpu_millis": 4000,
  "quota_memory_mib": 8192,
  "quota_pods": 50,
  "quota_persistent_volume_claims": 10,
  "quota_services": 25
}
```

Then register one target for that environment:

```http
POST /api/v1/workspaces/{workspace_id}/infrastructure-targets
```

```json
{
  "environment_id": "<environment UUID>",
  "repository_url": "https://github.com/acme/platform-infra",
  "source_ref": "main",
  "working_directory": "environments/staging",
  "tool": "opentofu",
  "backend_workspace": "deployforge-staging",
  "enabled": true
}
```

Only canonical HTTPS GitHub repositories and repository-relative working directories are accepted. Production environments default to protected. Only a platform administrator can remove production protection or enable production destroy plans.

## Run and approve a plan

- `PATCH /api/v1/workspaces/{workspace_id}/environments/{environment_id}` — update namespace, protection, destroy guard, and quotas.
- `GET/POST /api/v1/workspaces/{workspace_id}/infrastructure-targets` — list or register targets.
- `PATCH /api/v1/workspaces/{workspace_id}/infrastructure-targets/{target_id}` — update or disable a target.
- `POST /api/v1/workspaces/{workspace_id}/environments/{environment_id}/infrastructure-runs` — queue `{ "run_kind": "plan" }` or `{ "run_kind": "drift" }`.
- `GET /api/v1/workspaces/{workspace_id}/infrastructure-runs` — list runs, optionally filter by environment and status.
- `GET /api/v1/workspaces/{workspace_id}/infrastructure-runs/{run_id}` — inspect saved plan metadata and ordered events.
- `POST /api/v1/workspaces/{workspace_id}/infrastructure-runs/{run_id}/approve` — approve a changed plan as a different authorized operator; this queues apply of the exact saved plan digest.
- `POST /api/v1/workspaces/{workspace_id}/infrastructure-runs/{run_id}/reject` — reject a pending plan.
- `POST /api/v1/workspaces/{workspace_id}/infrastructure-runs/{run_id}/retry` — requeue a blocked plan after fixing provider or environment configuration.
- `POST /api/v1/infrastructure-runs/{run_id}/callback` — receive the HMAC-authenticated workflow result.

The control plane permits one active run per environment. Configured quota dimensions require corresponding nonnegative projected usage in the signed plan callback; missing or over-limit usage blocks apply. A protected production plan that includes resource destruction is blocked unless a platform administrator explicitly enables that capability. Runs, approvals, rejections, callbacks, and failures are audit events. The first deployment should be a non-production plan/apply exercise with a disposable remote backend.

