# Azure DevOps Pipelines

DeployForge can queue a build in an Azure DevOps Services YAML pipeline. The adapter dispatches only repositories and pipeline IDs explicitly present in `DEPLOYFORGE_AZURE_DEVOPS_PIPELINE_IDS`; it pins that ID on the durable build record, passes the requested branch or tag, and supplies callback/run metadata as pipeline variables. The worker sends credentials to Azure DevOps; the API receives only the public organization, issuer, and repository mapping.

## Configure the adapter

Set these values in the worker and API configuration:

```dotenv
DEPLOYFORGE_BUILD_PROVIDER=azure_devops
DEPLOYFORGE_AZURE_DEVOPS_ORGANIZATION_URL=https://dev.azure.com/acme-platform
DEPLOYFORGE_AZURE_DEVOPS_OIDC_ISSUER=https://vstoken.dev.azure.com/00000000-0000-0000-0000-000000000000
DEPLOYFORGE_AZURE_DEVOPS_PIPELINE_IDS={"Payments/payments-api":{"pipeline_id":42,"config_path":"azure-pipelines.yml","signer_identity":"https://deployforge.example.com/signers/acme-platform/payments-api"}}
DEPLOYFORGE_BUILD_CALLBACK_PUBLIC_URL=https://deployforge.example.com
```

For the current Azure DevOps issuer, replace the example with `https://vstoken.dev.azure.com/{organization-guid}`. The key in the JSON map is `Project/Repository`; the pipeline ID must be the numeric Azure DevOps pipeline ID. `signer_identity` is the exact certificate identity that the private Fulcio profile will issue for this pipeline. Keep this identity stable and restricted to the mapped pipeline. The optional `config_path` is descriptive metadata for the pipeline definition.

For production Compose, place the private trust root at `secrets/sigstore_trusted_root.json`, set `DEPLOYFORGE_SIGSTORE_TRUSTED_ROOT_FILE=/run/secrets/sigstore_trusted_root.json`, and set `DEPLOYFORGE_SIGSTORE_TRUSTED_ROOT_SHA256` to its lowercase SHA-256 digest. The API mounts the trust root read-only; the worker validates that the file path and checksum are configured before starting.

The production Compose overlay reads the worker-only token from `secrets/azure_devops_build_token`. Helm reads the `azure_devops_build_token` key from its existing runtime Secret and injects it only into worker pods. Create a short-lived PAT with only the Build (Read & execute) scope and access limited to the mapped projects/pipelines. Azure DevOps recommends Microsoft Entra tokens for long-running production integrations; this adapter currently uses PAT Basic authentication, so rotate the PAT frequently, monitor its expiry, and treat Entra-backed token acquisition as a remaining hardening item. See Microsoft's [authentication guidance](https://learn.microsoft.com/en-us/azure/devops/integrate/get-started/authentication/authentication-guidance?view=azure-devops) and the [Run Pipeline API](https://learn.microsoft.com/en-us/rest/api/azure/devops/pipelines/runs/run-pipeline?view=azure-devops-rest-7.0).

The supported repository URL form is `https://dev.azure.com/{organization}/{project}/_git/{repository}`. Register that canonical HTTPS URL on the service. Azure DevOps Server and `visualstudio.com` aliases are not accepted by this adapter.

## Supply-chain trust requirements

Azure DevOps OIDC is not included in Sigstore's public Fulcio issuer profile, and its token audience is `api://AzureADTokenExchange`. When production verification is enabled, DeployForge therefore requires a custom Sigstore trust root and SHA-256 pin. Configure the private Fulcio/Rekor profile to accept only the Azure organization issuer, the Azure DevOps token audience, and the expected pipeline subject. Map the exact pipeline subject to the `signer_identity` URI from the repository binding. Do not add the Azure issuer to a broad identity rule or relax the certificate identity check. See Azure's [OIDC service connection guidance](https://learn.microsoft.com/en-us/azure/devops/pipelines/release/configure-workload-identity?view=azure-devops) and Sigstore's [Fulcio OIDC identity reference](https://github.com/sigstore/fulcio/blob/main/docs/oid-info.md).

The pipeline must obtain its short-lived OIDC ID token using `System.OidcRequestUri` and `System.AccessToken`, pass that token explicitly to Cosign, and use the private Fulcio configuration. Azure DevOps documents `System.AccessToken` as the token for its REST API; YAML must explicitly map it into the task environment. The pipeline must also emit SLSA provenance whose subject digest is the published image digest, whose source URI/ref/digest match the checked-out Azure repository, and whose build-config identity matches the configured signer identity. DeployForge independently checks the signed artifact, provenance, SBOM, and digest-bound vulnerability summary against the pinned custom root. Public Fulcio and GitHub's hosted attestation action are not drop-in replacements for this Azure DevOps trust profile. Azure's current `vstoken.dev.azure.com` workload-identity issuer is scheduled for retirement on July 1, 2027; keep the issuer and private Fulcio profile aligned with Microsoft's migration guidance for [Entra-issued workload identity tokens](https://learn.microsoft.com/en-us/azure/devops/pipelines/release/configure-workload-identity?view=azure-devops). Microsoft documents the relevant runtime variables in [Azure Pipeline predefined variables](https://learn.microsoft.com/en-us/azure/devops/pipelines/build/variables?view=azure-devops).

## Pipeline callback contract

The dispatch supplies these variables:

- `DEPLOYFORGE_BUILD_RUN_ID`
- `DEPLOYFORGE_DEPLOYMENT_ID`
- `DEPLOYFORGE_CALLBACK_URL`
- `DEPLOYFORGE_SIGNER_IDENTITY`
- `DEPLOYFORGE_OIDC_ISSUER`

Run the repository-owned build, scan, sign, SBOM, and provenance steps. After checkout, compare `Build.SourceVersion` with the checked-out commit, and restrict the pipeline to the intended protected refs and environments. In an `always()` reporting step, set `BUILD_STATUS` from `Agent.JobStatus`, `SOURCE_COMMIT_SHA` from `Build.SourceVersion`, `DEPLOYFORGE_CALLBACK_SECRET` from a protected secret variable, and the evidence paths/URIs expected by `examples/demo-service/scripts/report-build.py`. The helper derives the canonical repository URL, run URL, and branch/tag from Azure's predefined variables, validates the HTTPS callback host, and signs the callback with HMAC. Never echo the callback secret or OIDC token.

The example reporter is a callback/evidence helper, not a complete Azure pipeline definition: image registry access, private Fulcio, provenance generation, and the GitOps repository write must be configured for the organization's platform. Terraform/OpenTofu infrastructure plans and GitHub Golden Path repository creation continue to use their existing GitHub Actions integrations.

## Operational behavior

Azure DevOps 429 responses, server errors, network failures, and timeouts use the durable build retry schedule. Permission errors and invalid pipeline/ref configuration fail the build with an actionable provider message. A returned run URL is accepted only when it remains under the configured organization and project. The callback repository, run ID, HMAC, expected signer identity, issuer, source revision, and evidence are checked before a release advances.

For a basic PAT, set an expiry alert and rotate it before expiry. For production, evaluate an Entra-backed token provider before making the adapter a long-lived critical path. Keep the Azure DevOps organization GUID issuer, private trust root, and signer-identity map under change control.
