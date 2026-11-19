# Incident response

DeployForge stores an operational incident record separately from its source signal. Use the incident record as the workspace response log; the original alert, deployment, or GitOps run remains the source evidence.

## Intake and deduplication

Workspace administrators, platform engineers, and service owners can open a manual incident or convert a firing SLO alert, failed deployment, or failed/blocked GitOps run from the Incidents page. Conversion is unique per workspace and source record, so repeated clicks or retries return the existing incident rather than opening duplicates. Signal-derived service and environment scope is copied from the source record. New incidents start in `open` and are sent to PagerDuty by the worker when a routing key is configured.

The incident API is under `/api/v1/workspaces/{workspace_id}/incidents`. List requests support bounded `limit`, `offset`, `status`, and `severity` filters. Response events are ordered by a per-incident sequence and are append-only. Mutations are written in the same database transaction to the incident timeline and the workspace audit log.

## Response, ownership, and escalation

Assign an active workspace member as responder. The lifecycle accepts these transitions:

| Current state | Allowed next states |
| --- | --- |
| `open` | `investigating`, `mitigated`, `resolved` |
| `investigating` | `mitigated`, `resolved` |
| `mitigated` | `investigating`, `resolved` |
| `resolved` | `investigating` (reopen), `closed` |
| `closed` | terminal |

Resolution requires a human-readable summary. Reopening clears the prior resolution timestamp and summary and records the reason in the timeline. Closed incidents cannot be changed.

The escalation action increments a durable workspace-side level, can reassign a responder, and can record a next escalation time. This is an application ledger only. Configure PagerDuty schedules, escalation policies, urgency, notification rules, conference bridges, and responder acknowledgement in PagerDuty; DeployForge does not claim to run a replacement on-call scheduler. The Events API delivery uses one stable deduplication key per incident, maps SEV0–SEV3 to PagerDuty critical/error/warning/info, retries failed delivery from the worker, and sends a resolve event after a triggered incident is resolved or closed.

Set the PagerDuty routing key only in the worker's secret store or worker environment. Production API containers must not receive it. Validate a trigger and resolve in a non-production PagerDuty service before enabling the key for a production worker.

## Postmortem review

After resolution, the responder can save a draft with summary, customer impact, root cause, detection, response, and corrective actions. Submitting a draft moves it to independent review. The author cannot review their own report. A reviewer can approve and publish it or return it to draft with comments; every step is included in both the incident event timeline and audit events. Export audit history through the configured immutable audit export workflow when retention outside the primary database is required.

Record an owner and due date in each corrective action. Review overdue actions in the regular service reliability review; the current product stores these actions in the reviewed postmortem but does not yet create a separate corrective-action task queue or enforce due-date reminders.

## Operator checklist

1. Confirm the source signal and affected service/environment.
2. Open or reuse its incident record, set severity, and assign the active responder.
3. Record meaningful investigation, escalation, mitigation, and handoff notes as each decision occurs.
4. Resolve only after service recovery is verified; capture the remediation and any customer follow-up.
5. Complete the postmortem, submit for independent review, and track each corrective action to completion.
6. If a trigger or resolve notification is missing, check the worker logs, PagerDuty Events API delivery state on the incident, worker health, and the configured secret. Do not create a duplicate incident to retry delivery; the worker retries against the same deduplication key.
