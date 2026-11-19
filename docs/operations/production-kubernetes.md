# Production Kubernetes deployment

The Helm chart in `deploy/helm/deployforge` is the production-oriented deployment path. It runs multiple API, worker, and dashboard replicas, keeps database migrations in a one-time Helm hook Job, applies disruption budgets, and can create CPU-based autoscalers and an HTTPS ingress.

DeployForge deliberately does not install PostgreSQL. Before installing the chart, provision a managed HA PostgreSQL service with TLS, private networking, provider point-in-time recovery, monitoring, and a least-privilege application role. Set its connection limit with the selected API/worker replica counts in mind. The optional chart backup job separately creates encrypted, validated logical dumps in an S3-compatible object store.

## Prepare external dependencies

1. Create a namespace and configure an ingress controller, DNS, and a TLS Secret (or cert-manager integration).
2. Configure the IdP OIDC client with the exact redirect URI `https://<host>/api/v1/auth/callback`. Enforce MFA and conditional-access policy at the IdP.
3. Provision the external PostgreSQL database and verify its CA/TLS requirements from the Kubernetes network.
4. Create `deployforge-runtime` through the cluster's secret manager integration. It must contain these keys:

   - `database_url`: `postgresql+psycopg://...` for the managed database.
   - `database_ca.crt`: the managed database provider's root CA; the chart mounts it for `verify-full` TLS validation.
   - `session_secret`: at least 32 characters, shared by every API replica.
   - `oidc_client_secret`: the OIDC client secret.
   - `build_callback_secret`: at least 32 characters, shared with the configured GitHub workflow.
   - `github_repository_token`: required only when `config.github.repositoryProvisioning.enabled=true`; mount it into the worker only and grant the template read/repository creation plus Actions and Variables write permissions described in the [repository bootstrap guide](../integrations/github-repositories.md).
   - `scim_bearer_token`: required only when `config.scim.enabled=true`; use a separate random token for the IdP's SCIM connector.
   - `github_actions_token`: required only when `config.github.enabled=true`.
   - `gitlab_build_token`: required when `config.buildProvider=gitlab_ci`; mount it into the worker only and scope the project access token to the project(s) DeployForge may build.
   - `azure_devops_build_token`: required when `config.buildProvider=azure_devops`; mount it into the worker only and scope its Build Execute permission to the mapped pipelines. The current adapter uses PAT Basic authentication; use a short expiry and rotation policy.
   - `argocd_auth_token`: required only when `config.argocd.enabled=true`.
   - `flux_auth_token`: required only when `config.flux.enabled=true`; use a dedicated Kubernetes API identity scoped to the Flux Kustomizations, GitRepositories, and workload Pods DeployForge must inspect.

   Do not commit Kubernetes Secret manifests or secret values. Prefer External Secrets Operator, Secrets Store CSI, or the cloud provider's workload identity integration.

5. Set the image repositories/tags, hostname, OIDC values, and enabled integrations in a protected values file. The example `values.yaml` contains safe feature defaults; replace every `example.com` value before deployment.

Configure OIDC group claims and `config.oidc.groupRoleMappings` for workspace roles. The SCIM connector uses the pre-provisioned user's `externalId`; set `config.oidc.scimExternalIdClaim` to the matching OIDC UserInfo claim and enable `config.scim.enabled`. Follow the [identity lifecycle and review runbook](identity-governance.md) for supported SCIM operations, quarterly access review, MFA, and emergency IdP accounts.

For GitHub Enterprise Server, set `config.github.serverUrl` to the GitHub web origin and `config.github.apiUrl` to its REST API base. If its private Sigstore service is used, configure `config.supplyChain.oidcIssuer`, mount its `trusted_root.json` through `runtimeSecretMounts`, and set both `trustedRootFile` and the exact lowercase `trustedRootSha256`. For example, a projected Secret item at `sigstore/trusted_root.json` is available to the API at `/run/secrets/sigstore/trusted_root.json`. The API verifies the configured checksum before constructing the verifier. Roll all API replicas when rotating the root and pin together.

To use GitLab CI for service builds, set `config.buildProvider=gitlab_ci`, configure `config.gitlab.serverUrl`, `config.gitlab.apiUrl`, and `config.gitlab.ciConfigPath`, and add `gitlab_build_token` to the runtime Secret. The chart injects that credential only into worker pods. Keep GitHub Actions enabled separately if Terraform/OpenTofu or repository bootstrap still depends on it. See the [GitLab CI integration guide](../integrations/gitlab-ci.md) for token scope, runner setup, OIDC identity policy, and callback requirements.

To use Azure DevOps, set `config.buildProvider=azure_devops`, configure `config.azureDevOps.organizationUrl`, `oidcIssuer`, and the JSON `pipelineIds` map, then add `azure_devops_build_token` to the runtime Secret. The chart injects the token only into workers. Azure DevOps production builds require a private Fulcio profile and a checksum-pinned custom Sigstore trust root mounted for API verification. See the [Azure DevOps integration guide](../integrations/azure-devops.md) for pipeline mapping, token limits, OIDC claims, and callback requirements.

To use Flux, enable `config.flux.enabled`, set `config.flux.apiUrl` and `config.flux.clusterName`, and add `flux_auth_token` to the runtime Secret. The chart injects this token only into workers. Bind the identity to the minimum namespaced Kubernetes permissions described in the [Flux integration guide](../integrations/flux.md). Give each DeployForge target a dedicated GitRepository that is not managed by another Kustomization; the worker updates its `spec.ref.commit` to the exact deployed revision. Verified OCI deployments also require the target label on workload Pod templates.

To enable Golden Path repository creation, configure `config.github.repositoryProvisioning.owner` and the JSON template map, then set `config.github.repositoryProvisioning.enabled=true`. Add `githubRepositoryToken` to the existing runtime Secret. The chart injects this token into the worker only; see the [GitHub repository bootstrap guide](../integrations/github-repositories.md) for least-privilege setup.

For scheduled off-site backups and audit exports, configure a dedicated S3 bucket or separately restricted prefixes with versioning, Object Lock enabled, a default encryption policy, writer-only permissions, and lifecycle/retention controls. Create an age key pair outside the cluster; put only its public recipient in chart values and keep the private identity in a separate recovery secret manager. Bind the backup and audit-export ServiceAccounts to separate short-lived cloud workload identities. Enable `backup.enabled`, set its bucket/region/recipient and provider-specific ServiceAccount annotation. The default CronJob runs every six hours and requests a 90-day Compliance-mode Object Lock retention for each encrypted dump and checksum. Enable `auditExport.enabled` with its bucket and ServiceAccount annotation; it exports audit events every five minutes by default and requests 365-day Compliance retention. Do not grant the API or worker ServiceAccounts access to either export prefix.

## Install and upgrade

```sh
helm upgrade --install deployforge deploy/helm/deployforge \
  --namespace deployforge --create-namespace \
  --values /path/to/protected-production-values.yaml \
  --wait --timeout 20m
```

The chart runs `alembic upgrade head` as a pre-install/pre-upgrade Job. API containers start Uvicorn directly and never race to migrate the database. Failed migrations stop the Helm operation before the new API rollout. Review migration compatibility for rolling deployments and take a database snapshot before schema changes that are not backward compatible.

The dashboard service is the only ingress target; its Nginx proxy sends `/api/v1/` to the internal API service. The API Service is cluster-internal. TLS termination, request size limits, rate limits, WAF policy, and trusted forwarded-header handling belong at the ingress layer. Apply ingress-controller annotations for rate limiting where supported.

APK inspection is optional. Set `apkParser.enabled=true` to deploy the bounded parser as an internal ClusterIP workload. The chart applies a NetworkPolicy that accepts parser connections only from DeployForge API pods. If the chart-wide egress NetworkPolicy is enabled, include the parser Service on TCP 8100 in the API egress rules. The 65 MiB ingress limit accommodates the parser's 64 MiB upload bound. The parser extracts metadata and hashes only; it does not install, execute, or cryptographically validate APK signatures.

The example ingress values include conservative per-client request limits for ingress-nginx. Review and tune them for the actual controller and expected traffic; other ingress implementations may ignore these annotations. Keep TLS termination and trusted proxy configuration at the edge, and alert on 429 responses.

## Scaling and recovery

- API, worker, and dashboard replicas spread across zones/hosts where possible. CPU HPA is enabled by default; workers can be autoscaled between 2 and 12 replicas.
- Set HPA bounds and resource requests against measured database connections, queue age, and workload. CPU-only HPA does not replace queue-depth/backlog alerts.
- Configure alerting for pending/failed migration Jobs, unavailable replicas, worker heartbeat count, queue age, database connections, and backup/PITR status.
- The optional backup CronJob makes a full dump every six hours. Its practical RPO is the age of the latest successful, verified upload, so alert before that age exceeds the agreed target. Provider PITR is the separate mechanism for recovery between logical dumps.
- The optional audit-export CronJob reserves events in PostgreSQL before uploading each immutable object pair. Failed uploads resume the same batch, and every audit event is reserved once even if concurrent transactions commit out of timestamp order. Download JSONL/manifest pairs in `batch_sequence` order and verify them together with `python scripts/verify-audit-export.py batch-1.jsonl batch-1.manifest.json batch-2.jsonl batch-2.manifest.json`. Use `--expected-previous-hash` when checking a known chain anchor; the verifier checks payload hashes, event hashes, and continuity between manifests.
- Worker IDs are unique per process, so concurrent replicas report separate heartbeat rows and claim work under distinct lease owners.
- Run the bounded read-only load profile and worker/database/Argo failure drills in [the scale and recovery runbook](scale-and-failure-drills.md) before accepting production traffic. The example latency thresholds are a starting budget; validate them against workload SLOs.
- The chart's PDBs reduce voluntary disruption but do not provide zone resilience if the cluster or database is configured in one zone.
- For an application rollback, use a known compatible image digest and `helm rollback`. Database schema rollback is not automatic; use forward-fix migrations and the provider's tested restore/PITR runbook when necessary.

## Recovery objectives and operational boundary

Set explicit RPO and RTO targets with the database and cloud operations owners. The chart can schedule encrypted S3 backups and append-only audit copies when configured, but provider PITR, database HA, WORM bucket setup, and paging remain infrastructure responsibilities. Verify each backup object and its checksum. For a restore drill, download the encrypted dump and `.sha256` sidecar, run `sha256sum -c`, and use `scripts/restore-postgres-offsite.sh` with both the active and a newly provisioned empty database URL plus the age identity file. The script refuses the active database and any restore target containing application objects. Record the drill date, recovered point, data-loss window, recovery duration, and follow-up actions in the operations record. Do not accept production readiness until the team has demonstrated both a point-in-time restore and a logical-dump restore.
