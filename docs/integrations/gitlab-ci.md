# GitLab CI build integration

DeployForge can dispatch a pipeline in a registered GitLab project. The worker creates the pipeline through GitLab's project pipeline API, records its pipeline ID and URL, and accepts the same HMAC-authenticated callback contract used by GitHub Actions. The service repository owns build, scan, signing, attestation, and GitOps update steps.

## Configure GitLab

Set the provider on both the API and worker:

```dotenv
DEPLOYFORGE_BUILD_PROVIDER=gitlab_ci
DEPLOYFORGE_GITLAB_SERVER_URL=https://gitlab.com
DEPLOYFORGE_GITLAB_API_URL=https://gitlab.com/api/v4
DEPLOYFORGE_GITLAB_CI_CONFIG_PATH=.gitlab-ci.yml
DEPLOYFORGE_BUILD_CALLBACK_PUBLIC_URL=https://deployforge.example.com
```

For self-managed GitLab, use the HTTPS web origin and that instance's REST API base, typically `https://gitlab.example.com/api/v4`. The server URL must be an HTTPS origin. Register each service with its canonical GitLab HTTPS project URL, including subgroup paths. Its CI config path must match `DEPLOYFORGE_GITLAB_CI_CONFIG_PATH` and the project's configured CI/CD configuration file.

Create a project access token with the `api` scope when all build projects are in one project. If DeployForge must build several projects, use a group access token scoped to the smallest group containing those projects. The token must have permission to create pipelines for every registered project. Store this single credential as `DEPLOYFORGE_GITLAB_BUILD_TOKEN` only in the worker's secret manager or mounted secret file. Compose reads `secrets/gitlab_build_token`; Helm reads the `gitlab_build_token` key from `secrets.existingSecret`. The API never receives this token. Give the token an expiry, rotate it through the secret manager, and restart the worker.

Create a separate random callback HMAC secret with at least 32 characters. Store it as `DEPLOYFORGE_BUILD_CALLBACK_SECRET` for the API and as a protected, masked (and hidden where supported) GitLab CI/CD variable named `DEPLOYFORGE_CALLBACK_SECRET`. Do not pass this secret in the pipeline-create API request or echo it in job logs. Keep production release branches and tags protected, and restrict who can edit the configured CI file and included build scripts.

GitLab CI pipelines started through the project pipeline API have `CI_PIPELINE_SOURCE=api`. Use rules to reject other sources for this release job, for example:

```yaml
workflow:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "api"'
    - when: never

stages: [release]

deployforge-release:
  stage: release
  rules:
    - if: '$CI_PIPELINE_SOURCE == "api"'
  id_tokens:
    DEPLOYFORGE_SIGSTORE_ID_TOKEN:
      aud: sigstore
  script:
    # Build and push the image, then scan and create the signed evidence bundles.
    # Use DEPLOYFORGE_SIGSTORE_ID_TOKEN for Cosign/Sigstore signing and attestations.
    - ./ci/build-scan-sign-and-attest.sh
    # The shared reporter validates the callback origin/path and signs the evidence JSON.
    - BUILD_STATUS=success python examples/demo-service/scripts/report-build.py
  after_script:
    - if [ "$CI_JOB_STATUS" != "success" ]; then BUILD_STATUS=failure python examples/demo-service/scripts/report-build.py; fi
```

The control plane supplies `DEPLOYFORGE_BUILD_RUN_ID`, `DEPLOYFORGE_DEPLOYMENT_ID`, and `DEPLOYFORGE_CALLBACK_URL` as non-secret pipeline variables. The reporter accepts `DEPLOYFORGE_BUILD_RUN_ID` directly. It reads GitLab's `CI_PROJECT_URL`, `CI_PIPELINE_ID`, `CI_PIPELINE_URL`, `CI_COMMIT_SHA`, `CI_COMMIT_REF_NAME`, `CI_COMMIT_TAG`, `CI_CONFIG_PATH`, and `CI_SERVER_URL` values to form the callback and signer identity. The reporter also requires `DEPLOYFORGE_CALLBACK_ALLOWED_HOST`, set to the exact DeployForge API hostname, before it sends the HMAC secret. See [`report-build.py`](../../examples/demo-service/scripts/report-build.py) for the evidence JSON and callback signing implementation.

The example release script above is an integration skeleton: each repository must provide `ci/build-scan-sign-and-attest.sh` and write the evidence files/variables consumed by the reporter (`IMAGE`, `IMAGE_DIGEST`, `SBOM_PATH`, `VULNERABILITY_REPORT_PATH`, and the signature, provenance, SBOM, and scanner bundle paths). Do not report success unless every required step completed and each evidence bundle refers to the exact published artifact digest.

## Certificate identity and verification

The signing identity must be issued from the same project, config path, and ref that DeployForge dispatched. For example:

```text
https://gitlab.com/platform/payments//.gitlab-ci.yml@refs/heads/main
```

DeployForge requires the GitLab issuer to equal `DEPLOYFORGE_GITLAB_SERVER_URL`, and verifies the certificate's source repository URI, commit digest, full source ref, `api` pipeline trigger, build config URI, and build config digest. The reporter derives `signer_identity` and `oidc_issuer` from GitLab's runtime variables; do not hard-code them for another project or branch.

GitLab.com provides the relevant OIDC claims for supported CI tokens. On self-managed GitLab, configure the Fulcio identity mapping to put the GitLab project/config/ref in the certificate identity and include source-repository and build-config certificate extensions required by DeployForge. Keep the top-level CI config in the same protected project/revision as the build; any external or separately versioned configuration must be checked against the exact certificate claims before production use. The verifier intentionally rejects evidence when those claims are absent or differ.

GitLab's pipeline API response is only dispatch acceptance. The pipeline must send a `running` callback for long work when needed and one terminal signed `succeeded` or `failed` callback. A success callback must contain the artifact signature, in-toto/SLSA provenance, signed SBOM, and Grype result bundles. The verifier checks these independently against Sigstore's production trust root. Follow the shared [callback and evidence contract](github-actions.md#callback-protocol); its callback URL, HMAC headers, evidence limits, and retry behavior also apply to GitLab.

## Operational limits

- The selected build provider applies to service build runs. Terraform/OpenTofu runs and Golden Path repository creation currently use GitHub APIs and still need their GitHub worker credentials when enabled.
- Configure source project URLs on the same GitLab instance as `DEPLOYFORGE_GITLAB_SERVER_URL`; cross-instance or non-HTTPS repository URLs are rejected.
- A callback that reports a `gitops_revision` still requires a registered GitOps reconciler. DeployForge currently supports Argo CD for release reconciliation.
- Keep the project token and callback HMAC secret separate. Pipeline-create variables are visible to the job and must contain identifiers/URLs only, never credentials.

## References

- [GitLab Pipelines API](https://docs.gitlab.com/api/pipelines/)
- [GitLab ID token authentication](https://docs.gitlab.com/ci/secrets/id_token_authentication/)
- [GitLab self-managed Sigstore signing](https://docs.gitlab.com/ci/yaml/signing_self_hosted/)
- [GitLab Cosign/Sigstore signing examples](https://docs.gitlab.com/ci/yaml/signing_examples/)
