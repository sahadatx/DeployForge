# Release controls

## Deployment freeze windows

Workspace administrators and platform engineers can schedule one freeze window per environment from **Environments → Schedule freeze** or through the API:

```http
POST /api/v1/workspaces/{workspace_id}/environments/{environment_id}/deployment-freeze
Content-Type: application/json

{
  "starts_at": "2026-12-24T00:00:00Z",
  "ends_at": "2026-12-27T00:00:00Z",
  "reason": "Holiday change freeze approved by the platform team"
}
```

Both timestamps must be timezone-aware, the end must follow the start, and a window can run for at most 30 days. Scheduling or canceling a freeze is written to Audit Logs. Queued deployments remain in the durable queue, but workers do not start their validation while the window is active. Pending approvals return a conflict during the freeze, and queued Argo CD syncs wait until the window ends. An Argo CD operation that already started is allowed to finish; freezes do not attempt to interrupt an external rollout.

Cancel a scheduled window with `DELETE /api/v1/workspaces/{workspace_id}/environments/{environment_id}/deployment-freeze`.

## Progressive delivery boundary

DeployForge pins GitOps releases to immutable manifest revisions and waits for Argo CD to report the exact revision as `Synced` and `Healthy`. The repository includes an Argo Rollouts example at [`examples/progressive-delivery/checkout-canary.yaml`](../../examples/progressive-delivery/checkout-canary.yaml) with weighted canary steps and Prometheus analysis. Install the Argo Rollouts controller and CRDs in the workload cluster, adapt the metric query and traffic routing to the service, and commit the manifests in the service's protected GitOps repository.

Argo Rollouts runs the weighted steps and aborts a rollout when its analysis fails. DeployForge observes the containing Argo CD Application and succeeds only after its exact manifest revision becomes `Synced` and `Healthy`; a failed or timed-out rollout follows DeployForge's existing failed-release and rollback path. Set `DEPLOYFORGE_GITOPS_SYNC_TIMEOUT_SECONDS` above the total canary pauses, metric collection, and workload startup time. The example uses replica-based weights; production traffic shaping requires configuring and validating the chosen ingress or service-mesh traffic-routing provider.

DeployForge can persist bounded stage and AnalysisRun measurement summaries and queue stepwise promotion or abort for an active deployment rollout. Configure `DEPLOYFORGE_ARGO_ROLLOUTS_API_URL` for both API and worker and give the worker a dedicated `DEPLOYFORGE_ARGO_ROLLOUTS_AUTH_TOKEN`; the API must not receive this credential. Route the Rollouts API server through TLS and a trusted authentication proxy with least privilege. Promote is sent with `full=false` so remaining stages and analysis gates are not skipped. See the [progressive delivery guide](progressive-delivery.md) for setup and failure behavior.
