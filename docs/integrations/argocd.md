# Argo CD GitOps integration

DeployForge uses Argo CD as the Kubernetes reconciliation boundary. A successful CI callback creates a durable GitOps run for the service and environment target. The source commit identifies the code used to build the image; `gitops_revision` identifies the manifest commit containing the image digest. The worker asks Argo CD to sync the manifest revision, then polls the Application until that exact revision is `Synced` and `Healthy`.

## Configure the provider

Set these values for both the API and worker. Compose maps them from the root `.env` file:

```dotenv
DEPLOYFORGE_ARGOCD_API_URL=https://argocd.example.com
DEPLOYFORGE_ARGOCD_AUTH_TOKEN=<scoped Argo CD bearer token>
DEPLOYFORGE_ARGOCD_API_TIMEOUT_SECONDS=15
DEPLOYFORGE_GITOPS_SYNC_TIMEOUT_SECONDS=1800
DEPLOYFORGE_GITOPS_RECONCILE_INTERVAL_SECONDS=10
```

The API URL must use HTTPS. Keep TLS verification enabled and store the token in a secret manager outside local development. Scope the Argo CD credential to only the Applications and projects DeployForge must operate.

## Register an application target

Create an Argo CD Application first, then register its binding to a DeployForge service and environment:

```http
POST /api/v1/workspaces/{workspace_id}/gitops-targets
Content-Type: application/json
X-CSRF-Token: <session CSRF token>
```

```json
{
  "service_id": "<service UUID>",
  "environment_id": "<environment UUID>",
  "application_name": "payments-api-staging",
  "expected_project": "platform",
  "expected_cluster_name": "staging-cluster",
  "artifact_name": "payments-api",
  "enabled": true
}
```

`artifact_name` is optional when the build reports exactly one artifact. It is required when the workflow reports multiple artifacts. The registered Application must be a single-source Application whose repository matches the Service Catalog repository, whose project matches `expected_project`, and whose destination matches `expected_cluster_name` when provided. Mismatches block the rollout before a sync is requested.

## Reconciliation and recovery

- `GET /api/v1/workspaces/{workspace_id}/gitops-targets` lists target bindings.
- `GET /api/v1/workspaces/{workspace_id}/gitops-targets/{target_id}/status` reports provider connectivity and the current Argo CD sync, health, revision, and destination state.
- `GET /api/v1/workspaces/{workspace_id}/gitops-runs` lists rollout and rollback history; filter by `deployment_id` or `status`.
- `GET /api/v1/workspaces/{workspace_id}/gitops-runs/{gitops_run_id}` includes ordered reconciliation events.
- `POST /api/v1/workspaces/{workspace_id}/gitops-runs/{gitops_run_id}/retry` requeues a blocked run after its target or provider configuration is fixed.
- `POST /api/v1/workspaces/{workspace_id}/deployments/{deployment_id}/rollback` queues an audited rollback to the prior healthy revision for the target's latest deployment.

The worker stores each state transition and audit event. A release is marked successful only after the observed revision exactly matches the requested GitOps manifest commit and Argo CD reports `Synced` plus `Healthy`. Timeout or degraded health fails the run; when an earlier healthy revision exists, DeployForge queues an automatic rollback and only marks the deployment rolled back after Argo CD verifies that revision. A provider outage is exposed as disconnected/retryable state; no success is inferred from the sync request response alone.

DeployForge currently disables pruning on sync requests and does not delete resources as part of a release. The adapter supports single-source Applications; multi-source Applications and ApplicationSet-specific operations are outside this integration boundary. Health is based on the health status Argo CD reports for the Application and its configured resources; review the [Argo CD health assessment documentation](https://argo-cd.readthedocs.io/en/latest/operator-manual/health/) when defining custom resource health checks.

## Operational checks

1. Confirm the target status endpoint reports `connected` and the intended project, destination, revision, sync, and health state.
2. Run a non-production deployment and confirm the GitOps run reaches `succeeded` only after the exact commit is healthy.
3. Exercise a failed rollout in a disposable environment and confirm the prior healthy revision is restored before the deployment reaches `rolled_back`.
4. Confirm the worker heartbeat remains healthy while reconciliation is pending.

