# Progressive delivery with Argo Rollouts

DeployForge delegates progressive workload rollout to Argo Rollouts while retaining approval, artifact policy, immutable GitOps revision, health verification, and rollback controls in the control plane.

## Setup

1. Install the Argo Rollouts controller and CRDs in the target cluster. Configure Argo CD to manage the `Rollout`, `AnalysisTemplate`, and Service resources.
2. Copy [`examples/progressive-delivery/checkout-canary.yaml`](../../examples/progressive-delivery/checkout-canary.yaml) into the service's protected GitOps repository.
3. Replace the example container image with the verified image digest reported by the build. Update readiness checks, ports, labels, stable/canary Services, and the Prometheus query to match the workload.
4. Verify the analysis query returns one numeric success-rate result for the canary workload. The example requires at least 99% successful requests across three one-minute samples and tolerates two failed measurements.
5. For production traffic weights, configure a supported traffic-routing integration such as the cluster's ingress or service mesh and validate that the stable and canary Services receive the intended shares. The included example uses replica-based weights and does not control request-level traffic distribution.
6. Set `DEPLOYFORGE_GITOPS_SYNC_TIMEOUT_SECONDS` above the sum of rollout pauses, metric sampling, image startup, and expected Argo CD reconciliation time. The default is 30 minutes. Alert on releases approaching the deadline.
7. Bind the DeployForge environment to the Kubernetes namespace containing the Rollout. Stage controls remain unavailable until this namespace is configured.
8. To enable stage controls, deploy the Argo Rollouts API server behind a trusted TLS ingress or authentication proxy. Configure `DEPLOYFORGE_ARGO_ROLLOUTS_API_URL` on the API and worker, and provide `DEPLOYFORGE_ARGO_ROLLOUTS_AUTH_TOKEN` only to the worker. Grant that identity read access to Rollout and AnalysisRun status plus only the promote and abort actions. For Compose production, mount the token at `secrets/argo_rollouts_auth_token`; for Helm, add `argo_rollouts_auth_token` to the runtime Secret and set `config.argoRollouts.enabled` and `config.argoRollouts.apiUrl`.

Argo Rollouts progresses through the weighted stages when measurements meet their success conditions and aborts when the configured failure limit is reached. DeployForge waits for Argo CD to observe the requested immutable manifest revision as `Synced` and `Healthy`. An abort, degraded health, or timeout fails the release and invokes the existing previous-revision recovery path when a previous healthy revision is available.

For an active deployment run, operators with workspace administrator or platform engineer access can promote the current paused stage or abort the active rollout from **GitOps → run details**. Promotion is always stepwise; DeployForge never requests full promotion, which would skip remaining stages and their analysis. Each action requires a reason, is audited, and enters a durable worker queue. The worker re-reads the rollout before dispatch; if the stage moved after the request it marks the action superseded. If a worker loses its lease after sending an action, the result becomes uncertain and is not automatically repeated.

The GitOps run detail stores a bounded snapshot of rollout phase, current step, replica counts, AnalysisRun names/phases, metric results, and recent measurements. Snapshots refresh during active GitOps reconciliation and appear in the run drawer with the action history. Automatic progression, metric evaluation, traffic routing, and analysis failure decisions remain controlled by Argo Rollouts and the configured traffic-routing provider. Protect the API server endpoint with TLS and a dedicated least-privilege identity; the application API receives the URL but not the worker token.

## References

- [Argo Rollouts canary strategy](https://argo-rollouts.readthedocs.io/en/stable/features/canary/)
- [Argo Rollouts analysis overview](https://argo-rollouts.readthedocs.io/en/stable/features/analysis/)
- [Argo Rollouts Prometheus provider](https://argo-rollouts.readthedocs.io/en/stable/analysis/prometheus/)
- [Argo Rollouts API](https://github.com/argoproj/argo-rollouts/blob/master/pkg/apiclient/rollout/rollout.proto)
- [Argo Rollouts stage promotion](https://argo-rollouts.readthedocs.io/en/stable/generated/kubectl-argo-rollouts/kubectl-argo-rollouts_promote/)
