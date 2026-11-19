# Scale and failure drills

Use the chart's multi-replica API and worker deployment with managed PostgreSQL. Run these drills in a production-like staging environment with synthetic or non-sensitive records. Capture queue depth and oldest-job age, worker heartbeats, API latency/error rate, database connections, build dispatches, and Argo CD run history before and after each drill.

## Read-only API load profile

The k6 profile reads the service catalog, release/build/GitOps queues, policies, and SLOs. It starts at 20 virtual users, ramps over one minute, holds for three minutes, then ramps down. It caps the requested profile at 100 users and sets thresholds of 750 ms p95, 1.5 s p99, and below 1% request failures.

Inject an authenticated session cookie through the approved secret manager or CI secret mechanism. Do not put the cookie value in shell history or commit it:

```sh
export BASE_URL="https://staging.example.com"
export WORKSPACE_ID="00000000-0000-0000-0000-000000000000"
export SESSION_COOKIE_NAME="deployforge_session"
: "${SESSION_COOKIE:?Inject a short-lived session cookie from your secret manager}"
k6 run scripts/k6-control-plane-smoke.js
```

Use a low starting profile and increase `VUS` in bounded steps. This read-only profile does not measure workflow dispatch throughput or deployment capacity; record the measured environment, replica counts, request rate, latency percentiles, error rate, and queue metrics with each run. Treat the example thresholds as an initial budget and set service-specific SLOs before production acceptance.

## Worker and provider recovery drills

Run one scenario at a time and verify the persisted event timeline as well as the Prometheus metrics:

1. **Worker termination after claim:** stop one worker while it owns a queued deployment or GitOps dispatch. Confirm another replica recovers the expired lease, attempt count remains within policy, no duplicate external dispatch occurs, and the queue-age gauge returns to zero after the queue drains.
2. **Worker scale-out:** increase workers while several independent jobs are queued. Confirm distinct worker heartbeats, one claim per job, stable database connection counts, and decreasing queue age. Scale back down and verify leases recover for any interrupted jobs.
3. **Argo CD outage:** make the provider endpoint temporarily unavailable in staging. Confirm retryable dispatches back off, the deployment remains non-successful, and healthy service is restored when Argo CD returns. Separately make a disposable Rollout fail its analysis and verify the previous healthy revision recovery path.
4. **Database failover:** trigger the managed database's staging failover procedure. Confirm API readiness reflects connection recovery, workers resume durable jobs, callback deliveries remain idempotent, and no job is marked successful from a lost request.
5. **Callback replay:** replay an already accepted signed callback delivery. Confirm the delivery idempotency constraint prevents duplicate artifact records and release transitions.
6. **Backup and audit continuity:** complete the restore drill in the production Kubernetes runbook, then verify the audit export chain across multiple consecutive manifests.

Record the trigger, expected behavior, observed recovery time, queue age, duplicate count, and follow-up action for each drill. Set workload-specific concurrency and recovery limits from these measurements; the default chart replica bounds are starting values, not capacity guarantees.
