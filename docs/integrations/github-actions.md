# GitHub Actions build integration

DeployForge owns the durable release request and job state. Each service repository owns its build steps because the runtime, registry, SBOM generator, scanner, and signing system vary by service.

## Configure the provider

Set these values for both the API and worker. Keep credentials in a secret manager outside local development.

| Setting | Purpose |
| --- | --- |
| `DEPLOYFORGE_GITHUB_ACTIONS_TOKEN` | Repository-scoped credential used to dispatch the workflow |
| `DEPLOYFORGE_BUILD_PROVIDER` | Selects `github_actions` (default), `gitlab_ci`, or `azure_devops` for service builds |
| `DEPLOYFORGE_GITHUB_SERVER_URL` | Canonical HTTPS origin for GitHub.com or one GitHub Enterprise Server instance |
| `DEPLOYFORGE_GITHUB_API_URL` | GitHub REST API base URL |
| `DEPLOYFORGE_GITHUB_ACTIONS_WORKFLOW_PATH` | YAML file under `.github/workflows` to dispatch |
| `DEPLOYFORGE_BUILD_CALLBACK_SECRET` | At least 32 random characters shared with the runner callback client |
| `DEPLOYFORGE_BUILD_CALLBACK_PUBLIC_URL` | HTTPS base URL reachable from the runner |
| `DEPLOYFORGE_BUILD_CALLBACK_TIMEOUT_SECONDS` | Maximum time to wait for a final callback; default is four hours |
| `DEPLOYFORGE_BUILD_MAX_CRITICAL_VULNERABILITIES` | Critical finding limit; default is zero |
| `DEPLOYFORGE_BUILD_MAX_HIGH_VULNERABILITIES` | High finding limit; default is zero |
| `DEPLOYFORGE_REQUIRE_VERIFIED_SUPPLY_CHAIN` | Require Sigstore signature and SLSA provenance verification; mandatory in production |
| `DEPLOYFORGE_SIGSTORE_OIDC_ISSUER` | Exact OIDC issuer expected in the signing certificate |
| `DEPLOYFORGE_SIGSTORE_TRUSTED_ROOT_FILE` | Optional mounted Sigstore `trusted_root.json` for a private instance |
| `DEPLOYFORGE_SIGSTORE_TRUSTED_ROOT_SHA256` | Required SHA-256 pin when a custom trust root file is configured |

Use a GitHub fine-grained token limited to the repositories DeployForge can build and grant it Actions write permission. The workflow-dispatch API requires that permission for fine-grained tokens. [GitHub workflow REST API permissions](https://docs.github.com/en/rest/actions/workflows#fine-grained-access-tokens-for-create-a-workflow-dispatch-event)

## Workflow contract

The configured workflow must declare `workflow_dispatch` inputs with these exact names because DeployForge sends them to the dispatch endpoint:

```yaml
on:
  workflow_dispatch:
    inputs:
      build_run_id:
        description: DeployForge build run UUID
        required: true
        type: string
      deployment_id:
        description: DeployForge deployment UUID
        required: true
        type: string
      source_ref:
        description: Branch, tag, or immutable commit requested by DeployForge
        required: true
        type: string
      callback_url:
        description: Signed build-result callback endpoint
        required: true
        type: string

permissions:
  contents: read
  id-token: write
```

Pin third-party actions to full commit SHAs and keep `GITHUB_TOKEN` permissions as narrow as the build requires. GitHub documents full-SHA pinning as the immutable option for action references. [GitHub secure use reference](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions#using-third-party-actions)

The workflow checks out `source_ref`, performs the repository's normal build/test steps, creates and scans an SBOM, publishes an immutable artifact, signs it, and creates provenance. Before exiting, it reports a terminal `succeeded` or `failed` callback. It may send `running` callbacks to refresh the timeout while a long build is active.

The repository includes a concrete sample at [`.github/workflows/deployforge-build.yml`](../../.github/workflows/deployforge-build.yml). It builds only `examples/demo-service`, publishes a commit-tagged GHCR image, scans that exact digest, creates a CycloneDX SBOM, signs the image with Cosign, emits GitHub SLSA provenance and SBOM attestations, and updates the digest-pinned manifest on `deployforge/staging`. The callback helper at [`examples/demo-service/scripts/report-build.py`](../../examples/demo-service/scripts/report-build.py) includes both the source commit and generated GitOps commit, then signs the exact request body with the configured HMAC secret. This staging sample is dispatched only by DeployForge and accepts only the protected `main` branch. It needs `contents: write` so its repository-scoped `GITHUB_TOKEN` can update the generated branch; keep that branch protected from force pushes and restrict updates to the release workflow.

The GitHub runner must reach the callback URL over HTTPS. Configure the repository variable `DEPLOYFORGE_CALLBACK_ALLOWED_HOST` to the exact API hostname (for example, `deployforge.example.com`) and the repository secret `DEPLOYFORGE_CALLBACK_SECRET` to the matching HMAC secret. The workflow checks the hostname and build-specific callback path before sending the secret in a request. The local `http://localhost:8000` API cannot receive a callback from a hosted runner.

## GitHub Enterprise Server and private Sigstore

For GitHub Enterprise Server, set `DEPLOYFORGE_GITHUB_SERVER_URL` to the instance origin, such as `https://github.enterprise.example`, and set `DEPLOYFORGE_GITHUB_API_URL` to its REST API base, commonly `https://github.enterprise.example/api/v3`. Use a repository-scoped token for that same instance. For its default Actions issuer, configure `DEPLOYFORGE_SIGSTORE_OIDC_ISSUER=https://github.enterprise.example/_services/token`; use the exact issuer returned by the instance's OIDC discovery document. GitHub documents the GHES issuer format as `https://HOSTNAME/_services/token` in its [OIDC reference](https://docs.github.com/en/enterprise-server%403.21/actions/reference/security/oidc).

For a private Sigstore deployment, mount that instance's serialized `trusted_root.json` into the API container. Set `DEPLOYFORGE_SIGSTORE_TRUSTED_ROOT_FILE` to its mounted path and `DEPLOYFORGE_SIGSTORE_TRUSTED_ROOT_SHA256` to the lowercase SHA-256 of the exact file bytes. The API checks the pin before loading Sigstore's trusted root; the public production trust root remains the default when no custom file is configured. Change the file and checksum together, then roll the API so every replica loads the same profile. Keep the root file in protected configuration and restrict who can change the checksum.

The verifier requires the certificate identity to match the configured GitHub server, repository, workflow path and source ref; it also validates GitHub workflow repository, commit SHA and `workflow_dispatch` certificate extensions. The custom Sigstore root changes which Fulcio and transparency-log keys are trusted. It does not disable the repository, workflow, commit, artifact digest, SBOM, provenance, or scanner-result checks.

## Callback protocol

DeployForge accepts callbacks at `POST /api/v1/build-runs/{build_run_id}/callback`. Sign the exact raw JSON request bytes as `HMAC-SHA256(secret, timestamp + "." + body)` and send:

```text
X-DeployForge-Timestamp: <Unix timestamp in seconds>
X-DeployForge-Signature: sha256=<lowercase hex digest>
Content-Type: application/json
```

The timestamp must be within five minutes of the API clock. Generate a unique `delivery_id` for every callback. The API rejects duplicate deliveries, mismatched repositories, conflicting commits/run IDs, expired signatures, and successful callbacks without artifact evidence.

A successful callback has this shape; use actual immutable locations and digests from the build rather than these example values:

```json
{
  "delivery_id": "github-run-987654321-finished",
  "repository_url": "https://github.com/acme/checkout-api",
  "external_run_id": "987654321",
  "run_url": "https://github.com/acme/checkout-api/actions/runs/987654321",
  "commit_sha": "0123456789abcdef0123456789abcdef01234567",
  "gitops_revision": "1234567890abcdef1234567890abcdef12345678",
  "status": "succeeded",
  "artifacts": [
    {
      "name": "checkout-api",
      "artifact_type": "container",
      "uri": "oci://ghcr.io/acme/checkout-api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "sbom_format": "cyclonedx-json",
      "sbom_uri": "https://artifacts.example.test/checkout-api/sbom.json",
      "sbom_digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "vulnerability_summary": {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "unknown": 0
      },
      "signature_uri": "https://artifacts.example.test/checkout-api/signature.bundle",
      "signer_identity": "https://github.com/acme/checkout-api/.github/workflows/deployforge-build.yml@refs/heads/main",
      "oidc_issuer": "https://token.actions.githubusercontent.com",
      "provenance_uri": "https://artifacts.example.test/checkout-api/provenance.jsonl",
      "provenance_digest": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
    }
  ]
}
```

`running` and `failed` callbacks may omit `artifacts`; a failed callback can include a short `failure_message`. Do not include credentials, tokens, raw environment dumps, or secret-bearing build logs in callback fields. Callback bodies are capped at 12 MiB. Signature, provenance, and scanner bundles are each capped at 100 KiB, 100 KiB, and 256 KiB respectively; an SBOM attestation bundle may be up to 6 MiB.

Each successful callback must report artifact, SBOM, vulnerability, signature and provenance details. `commit_sha` identifies the source revision used to build the artifact; optional `gitops_revision` identifies the separate manifest commit that Argo CD should reconcile. A production callback must include the complete Sigstore artifact-signature, SLSA provenance, SBOM, and Grype result attestation bundles as JSON objects in `signature_bundle`, `provenance_bundle`, `sbom_bundle`, and `vulnerability_evidence_bundle`. `sbom_digest` is SHA-256 over canonical JSON for the SBOM predicate, while `vulnerability_report_digest` is the SHA-256 digest of the exact Grype JSON report.

Production verification uses Sigstore's production trust root. For GitHub Actions, DeployForge checks the artifact digest, GitHub Actions issuer, workflow repository, workflow commit SHA and `workflow_dispatch` trigger. For GitLab CI, it checks the configured GitLab issuer, exact project/config/ref certificate identity, source repository URI/digest/ref extensions, API pipeline trigger, and config URI/digest extensions. Both providers must match the configured workflow/config path and source ref. For provenance, DeployForge verifies the DSSE signature, in-toto Statement v1 and SLSA Provenance v1, then checks that the subject contains the artifact digest and the source descriptor binds the registered source repository to the callback commit. The SBOM attestation is verified independently and its signed predicate format, subject digest, and canonical content digest are checked. The Grype result attestation is verified independently and binds the exact image digest, report digest, scanner version, and severity counts. `provenance_digest` is SHA-256 over the decoded signed statement bytes. `signature_uri`, `provenance_uri`, and SBOM locations remain audit references; DeployForge does not fetch arbitrary URLs to verify content.

If cryptographic evidence is invalid or missing, the callback is recorded as rejected and the build/deployment fails closed. If Sigstore trust services cannot be reached, the endpoint returns `503` with `Retry-After` so the workflow can retry. A production source ref must be a named branch or tag: use `main` for a branch and `refs/tags/v1.2.3` for a tag. Local development can continue to report evidence without bundles; those records remain `workflow_reported` and do not mean cryptographic verification succeeded.
