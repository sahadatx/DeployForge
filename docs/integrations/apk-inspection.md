# Isolated APK inspection

DeployForge can inspect Android APK metadata through a parser service that runs on a private container or cluster network. The API streams uploads to that service and records a workspace audit event containing the file hash, file size, filename, and package name. APK bytes and full parser output are not persisted by DeployForge.

Enable the parser in Compose by setting `DEPLOYFORGE_APK_PARSER_URL=http://apk-parser:8100`. In Kubernetes, set `apkParser.enabled=true`; the chart exposes only a ClusterIP Service and allows inbound parser traffic from API pods. The parser accepts files up to 64 MiB, and the API role must be `workspace_admin`, `platform_engineer`, or `developer` in the target workspace.

Send the APK as the raw HTTP request body with the normal authenticated session and CSRF headers:

```sh
curl --fail-with-body \
  -X POST "$DEPLOYFORGE_URL/api/v1/workspaces/$WORKSPACE_ID/apk/inspect" \
  -H "Cookie: deployforge_session=$SESSION" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H "Content-Type: application/vnd.android.package-archive" \
  -H "X-Artifact-Name: release.apk" \
  --data-binary @release.apk
```

The JSON response includes SHA-256, package and version metadata, SDK bounds, declared permissions, component names, DEX count, native ABIs, and signing certificate fingerprints when present. The parser does not execute or install the APK. It reports which signing blocks and certificates it found; `signature_validation` is always `not_performed`, so a signer fingerprint is not proof that the APK signature is valid or trusted.

Invalid APKs return `422`, files over the limit return `413`, and a saturated parser returns `429` with `Retry-After`. The upload is held in bounded temporary storage and removed after parsing. Keep the parser off public ingress and retain the container CPU and memory limits.
