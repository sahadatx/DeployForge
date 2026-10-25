# Staging operations runbook

This guide brings up one real release path on a single host. The Compose overlay is a **staging baseline**, not a highly available production deployment: it runs one API, one worker, and a local PostgreSQL container. Use managed PostgreSQL, an external secret manager, external backups, and a supported ingress for production.

## 1. Prepare host configuration

Requirements: Docker Engine with Docker Compose 2.24.4 or newer, OpenSSL, a DNS name, a trusted TLS reverse proxy, an OIDC provider, and a reachable GitHub Actions runner. The Compose dashboard binds only to `127.0.0.1:8080`. Configure the host proxy to send traffic there, set `X-Forwarded-Proto: https`, and overwrite forwarded headers supplied by clients. The proxy is responsible for certificates, HTTP-to-HTTPS redirect, request limits, and public network exposure.

Copy `.env.production.example` to `.env.production`, then set the dashboard origin, OIDC discovery URL, client ID, redirect URI, and administrator email allowlist. Register this exact redirect with the identity provider:

```text
https://<dashboard-host>/api/v1/auth/callback
```

Create local secret files and fill the OIDC secret through your secure editor or secret manager:

```sh
cp .env.production.example .env.production
./scripts/init-production-secrets.sh
```

`secrets/` is private on the host and ignored by Git. The bootstrap script generates separate PostgreSQL bootstrap and application-role passwords, plus session and callback keys. The API connects as the non-superuser `deployforge` database role; the PostgreSQL bootstrap password is mounted only into the database container and used by backup/recovery commands. Set the OIDC client secret in `secrets/oidc_client_secret` before starting the API. To enable GitHub Actions, put a repository-scoped GitHub token in `secrets/github_actions_token`, set `DEPLOYFORGE_BUILD_CALLBACK_PUBLIC_URL` to the HTTPS dashboard origin, and add the matching callback secret to the service repository's GitHub Actions secrets. To enable Argo CD, set its HTTPS API URL and put a least-privilege bearer token in `secrets/argocd_auth_token`. Leave the optional token files empty when those integrations are not configured. The API container receives neither the GitHub token nor the Argo CD token; the worker receives neither the login session key nor the OIDC secret.

Validate and start the stack:

```sh
docker compose --env-file .env.production \
  -f docker-compose.yml -f docker-compose.production.yml config --quiet
docker compose --env-file .env.production \
  -f docker-compose.yml -f docker-compose.production.yml up --build -d
```

The API applies Alembic migrations before opening its health endpoint. Check `docker compose ... ps` and the dashboard at `https://<dashboard-host>`. Production API docs and OpenAPI are disabled. The readiness route is available through the dashboard proxy at `/api/v1/health/ready`.

## 2. Create the staging platform records

Sign in through OIDC with an email in `DEPLOYFORGE_OIDC_ADMIN_EMAILS`; that verified account receives the Platform Admin role. In DeployForge:

1. Create a workspace such as `staging` and a `staging` environment. Bind the environment to the Kubernetes cluster and namespace used by Argo CD.
2. Add an ownership team, then register the test service with its canonical GitHub repository URL, source ownership, and criticality.
3. Create an enforced deployment policy that blocks critical/high findings and requires approval for the staging release as needed by the team.
4. Register the single-source Argo CD Application for the service/environment pair. Confirm its repository, project, and cluster binding match the service and environment.
5. Install the configured `workflow_dispatch` workflow in the service repository. It must build and publish an immutable image, generate and scan the SBOM, create a Sigstore signature bundle and an in-toto/SLSA provenance bundle, write the image digest to the protected GitOps branch, and send signed callbacks using the HMAC contract in [the GitHub Actions integration guide](../integrations/github-actions.md).
6. Use the protected `main` source branch for the bundled staging workflow. Production verification expects the GitHub workflow identity to match the configured workflow path and ref; other service workflows may use a protected branch or version tag if their manifest promotion flow supports it.
7. Request a staging deployment from Deployments, complete the required approval, and follow the build and GitOps run events.

The first complete release should show the following persisted path:

```text
queued → validating → policy_check → awaiting_approval → approved
       → building → policy_check → ready_to_deploy → deploying
       → verifying → succeeded
```

Every final artifact should have `evidence_status=verified`. The deployment should become successful only after Argo CD reports the exact immutable revision `Synced` and `Healthy`. A missing provider, rejected bundle, exceeded vulnerability threshold, policy deny, callback timeout, or unhealthy rollout should leave an auditable blocked/failed state. A failed rollout attempts recovery to the prior healthy revision when one is available.

## 3. Understand release evidence and trust boundaries

The callback HMAC authenticates the workflow sender and resists replay; it does not establish that an artifact is signed. In production, DeployForge separately verifies each artifact digest against its Sigstore bundle and verifies the bundle's production trust chain and workflow identity. It then verifies the signed SLSA provenance statement, checks the subject digest, and binds the source GitHub repository and commit to the registered service and callback.

The workflow-reported SBOM URL/digest and vulnerability counts are not downloaded or independently rescanned by DeployForge. Keep those producer steps inside a trusted workflow, pin its actions/tools, and make its scanner thresholds fail closed. The API does not fetch URLs supplied in evidence, avoiding arbitrary URL fetches from callback data. Trust-root refresh requires outbound access from the API to Sigstore's production trust services; if those services are unavailable, callbacks receive `503 Retry-After` and the workflow must retry.

## 4. Back up and restore PostgreSQL

Create a custom-format dump and validate its archive index:

```sh
./scripts/backup-postgres.sh
```

The script writes a mode-restricted timestamped file under `backups/`, removes partial dumps on failure, and validates the dump before publishing the final filename. Copy completed backups to a separate encrypted storage location and schedule this script with the host's approved scheduler. A backup left only on the Compose host does not protect against host or disk loss.

Restore into a **new database name**; the script refuses the active `deployforge` database:

```sh
./scripts/restore-postgres-to-new-db.sh backups/deployforge-<timestamp>.dump deployforge_restore_check
```

This operation does not change the running app. Validate the restored database, then perform a planned maintenance cutover by updating `secrets/database_url` to point at the restored database, stopping API/worker, and starting them against that URL. Keep the old database and backup until the restored service has been confirmed. Do not run `pg_restore --clean` against the active database as a routine recovery step.

## 5. Failure recovery

| Signal | First investigation |
| --- | --- |
| API is unhealthy | Inspect API startup and migration logs; verify the PostgreSQL healthcheck and `secrets/database_url`. |
| Worker heartbeat is stale | Inspect worker logs and PostgreSQL connectivity; provider calls run only in the worker. |
| Build is blocked | Check the registered GitHub repository, workflow path, token permissions, and HTTPS callback URL. |
| Build evidence is rejected | Compare the signer identity to the configured workflow path/ref; check Sigstore bundles, artifact digest, SLSA source commit, and `workflow_dispatch` certificate claims. |
| Verification returns `503` | Allow API outbound HTTPS to Sigstore trust services, then retry the same callback delivery. |
| GitOps is blocked or degraded | Check the Argo CD endpoint/token, Application project/source, cluster binding, and exact built image digest. Inspect run events before retry or rollback. |
| API database needs recovery | Stop API and worker, restore to a new database, verify it, then make a planned URL cutover. Preserve the original database for investigation. |

## 6. Before production traffic

This Compose baseline does not provide high availability, encrypted host files, centralized secret rotation, PostgreSQL point-in-time recovery, managed ingress, a telemetry storage backend, or an on-call pager. Replace these with organization-managed services and validate workload-specific SLOs, backup RPO/RTO, network policies, retention, incident response, and restore drills before production use. A successful staging release is evidence for this integration path; it is not a general production certification.
