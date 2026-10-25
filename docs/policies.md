# Policy-as-Code

DeployForge evaluates versioned, declarative JSON rules at deployment and infrastructure gates. The evaluator is deterministic and does not execute arbitrary Python, Rego, shell commands, or user-supplied expressions.

## Rule model

Each policy has a workspace scope, an action (`deployment`, `infrastructure`, or both), an enforcement mode (`enforce` or `monitor`), and up to 100 uniquely named rules. A rule matches one allowlisted context field with a typed operator and then applies an effect:

- `deny` blocks the workflow in `enforce` mode.
- `require_approval` records the additional gate. Deployment rules that match after artifact creation wait for another independent approver before GitOps; changed infrastructure plans already require an independent approval.
- `warn` records a finding without blocking.
- `monitor` mode converts matched effects into warnings and never blocks or requests approval.

The condition is the predicate that activates the effect. For example, this denies infrastructure plans that destroy more than one resource:

```json
{
  "id": "deny-large-destroy",
  "name": "Deny large destroy plans",
  "field": "infrastructure.resource_destroys",
  "operator": "greater_than",
  "expected": 1,
  "effect": "deny"
}
```

Supported fields include service lifecycle, criticality and tags; environment type and protection; deployment version/ref; reported artifact signature, SBOM, provenance and vulnerability summaries; and infrastructure tool, plan changes, resource counts and quota usage. Operators are `equals`, `not_equals`, `in`, `not_in`, numeric comparisons, `contains`, `not_contains`, `exists`, and `not_exists`. Rules on an absent field do not match; use `exists` or `not_exists` when presence itself is the control.

Policy updates use optimistic version checks. Each evaluation stores the selected policy documents, version numbers, context, per-rule outcomes and a SHA-256 snapshot digest so decisions can be reviewed later. Artifact rules are deferred until CI reports build evidence. Production deployments fail closed until an enabled enforce-mode deployment policy has a blocking or approval rule. Platform-owned environment quota and production destroy guards remain non-bypassable.

## Exceptions

An exception must target a specific service and/or environment, include a reason, and expire within 90 days. It remains pending until a different authorized operator approves it. Approval applies only to the exact policy version and rule ID requested. Updating a policy, expiry, rejection, or revocation stops the exception from suppressing findings. All requests and decisions are recorded in the audit log.

## API

- `GET/POST /api/v1/workspaces/{workspace_id}/policies`
- `GET/PATCH /api/v1/workspaces/{workspace_id}/policies/{policy_id}`
- `POST /api/v1/workspaces/{workspace_id}/policy-evaluations/evaluate` for a recorded dry-run
- `GET /api/v1/workspaces/{workspace_id}/policy-evaluations` for immutable results
- `GET/POST /api/v1/workspaces/{workspace_id}/policies/{policy_id}/exceptions`
- `POST /api/v1/workspaces/{workspace_id}/policy-exceptions/{exception_id}/decision`
- `POST /api/v1/workspaces/{workspace_id}/policy-exceptions/{exception_id}/revoke`

Policy administration and exception review require a workspace admin or platform engineer. Evaluation results are visible to workspace members. Mutating browser requests require the session CSRF token.

Artifact policy fields include values reported by the signed CI callback. In production, promotion also requires independent Sigstore artifact-signature and SLSA provenance verification; the API sets `all_signed` only after those checks pass. SBOM content and vulnerability counts remain producer-reported and are evaluated against the configured thresholds.
