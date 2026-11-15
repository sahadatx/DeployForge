# Immutable audit export

DeployForge can export audit events from PostgreSQL to an S3-compatible bucket. Each scheduled run reserves a batch in PostgreSQL before writing it, emits newline-delimited event records with a SHA-256 hash chain, and writes a manifest containing the batch range and payload checksum. The cursor advances only after both objects are stored. A database advisory lock prevents overlapping exporters; an interrupted pending batch is retried from its durable reservation. The exporter keeps one database connection for that lock and uses a second to commit reservations, so its connection pool is set to two connections.

## Configure storage and access

Use a dedicated bucket or isolated prefix with versioning and Object Lock enabled before enabling the exporter. Configure default encryption on the bucket. Keep the exporter service account separate from the API and worker; grant it only the bucket/prefix write and retention permissions it needs. If using KMS, grant the same identity the required encrypt/data-key permissions. Do not grant the application roles permission to delete exported objects or shorten retention.

Build and publish the exporter image from the repository root:

```sh
docker build -f deploy/audit-exporter/Dockerfile -t registry.example.com/deployforge-audit-exporter:0.1.0 .
docker push registry.example.com/deployforge-audit-exporter:0.1.0
```

Apply database migrations, then configure the Helm release. The CronJob is disabled by default:

```yaml
auditExport:
  enabled: true
  image:
    repository: registry.example.com/deployforge-audit-exporter
    tag: "0.1.0"
    # Prefer a digest in production.
    digest: ""
  bucket: deployforge-audit-prod
  prefix: deployforge/audit
  region: us-east-1
  kmsKeyId: ""
  objectLockMode: COMPLIANCE
  retentionDays: 365
  batchSize: 5000
  serviceAccount:
    annotations:
      eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/deployforge-audit-export
```

The bucket must already be versioned and have Object Lock enabled; the exporter requests retention on every payload and manifest object. `COMPLIANCE` cannot be shortened or bypassed before expiry. Choose `GOVERNANCE` only when your retention policy explicitly allows authorized administrators to bypass it. Retention must be at least 30 days. The exporter uses the pod's workload identity, the configured region and optional endpoint; it does not read long-lived AWS credentials from the application database or API container.

## Verify an export

Download the payload and manifest for each batch in ascending sequence order. Sequences are zero-padded in object names; actual keys also include their UTC date path and a batch ID. Pass each payload/manifest pair to the verifier, and pin the first batch's expected predecessor to 64 zeroes for a chain beginning at genesis:

```sh
python scripts/verify-audit-export.py \
  --expected-previous-hash "0000000000000000000000000000000000000000000000000000000000000000" \
  downloaded/audit-000000000001.jsonl downloaded/audit-000000000001.manifest.json \
  downloaded/audit-000000000002.jsonl downloaded/audit-000000000002.manifest.json
```

The verifier checks each object's SHA-256 checksum, event count, per-event hash, batch boundary and continuity between manifests. Store the final verified chain hash with the audit review record so a later review can anchor its first batch to the previously accepted export. A checksum or chain mismatch should be treated as an integrity incident and investigated against the protected bucket versions and exporter database records.

The source audit history remains in PostgreSQL; this exporter adds independent, retention-locked copies. Keep database backups and run restore drills separately. Review the configured retention period against the organization's audit and legal requirements before enabling Object Lock.
