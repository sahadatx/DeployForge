# Scheduled off-site PostgreSQL backups

The Helm chart can run an isolated Kubernetes `CronJob` to create an encrypted
PostgreSQL custom-format dump and store it in an S3-compatible bucket. Backups
are disabled by default. This job does not provide point-in-time recovery or a
restore drill; those are separate operational work.

## Prerequisites

- Use Kubernetes 1.27 or newer for the CronJob's explicit `timeZone` field.
- Use a dedicated bucket with versioning and Object Lock enabled when the bucket
  is created. Configure the bucket's default encryption as an additional storage
  control.
- Give the backup workload a dedicated cloud role with write access only to its
  configured prefix. The role needs bucket-location and object-write permissions,
  including permission to set Object Lock retention. Add KMS encryption
  permissions when using a customer-managed key.
- Generate an `age` recipient key pair outside the cluster. Configure only the
  public recipient in Helm. Keep the private identity in the organization's
  approved secret manager and recovery process; the backup job does not need it.
- Build and publish the backup image from the repository root. Pin its published
  digest in production rather than relying on a mutable tag:

  ```sh
  docker buildx build --push \
    --file deploy/backup/Dockerfile \
    --tag ghcr.io/your-org/deployforge-postgres-backup:0.1.0 .
  ```

The image includes PostgreSQL client tools, `age`, and the AWS CLI. The cluster
workload identity supplies object-storage credentials; do not put cloud keys in
Helm values or the application database secret.

## Helm configuration

Create the Kubernetes secret referenced by `database.existingSecret` with the
database URL under `database.urlKey` and the CA certificate under
`database.tls.rootCertKey` when TLS verification is enabled. Configure the backup
settings in a protected values file:

```yaml
backup:
  enabled: true
  schedule: "17 */6 * * *"
  timeZone: Etc/UTC
  image:
    repository: ghcr.io/your-org/deployforge-postgres-backup
    digest: sha256:REPLACE_WITH_PUBLISHED_IMAGE_DIGEST
  bucket: company-production-backups
  prefix: deployforge/postgres
  region: us-east-1
  ageRecipient: age1REPLACE_WITH_PUBLIC_RECIPIENT
  sseKmsKeyId: arn:aws:kms:us-east-1:123456789012:key/REPLACE_WITH_KEY_ID
  objectLock:
    mode: COMPLIANCE
    retentionDays: 90
  temporaryStorageLimit: 20Gi
  serviceAccount:
    annotations:
      eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/deployforge-backup
```

`sseKmsKeyId` is optional when bucket-level encryption is sufficient. For
non-AWS S3-compatible storage, configure `endpointUrl`, `region`, and an
equivalent workload identity mechanism supported by that provider. Object Lock
must be supported and enabled by the bucket before the first backup is run.

Adjust `temporaryStorageLimit` and the backup pod's ephemeral-storage resources
to exceed the expected compressed dump size. The default temporary volume limit
is 20 GiB. Keep the backup prefix dedicated to this workload so its role can be
restricted to that path. Dumps briefly exist unencrypted on the pod's temporary
volume before client-side encryption; use nodes with encrypted ephemeral storage
and restrict access to the backup namespace.

## Backup behavior and operations

Each run uses PostgreSQL TLS settings from the chart and reads its connection URL
from the existing Kubernetes secret. It writes a custom-format dump to private
temporary storage, checks that `pg_restore` can read the dump, encrypts it to the
configured `age` recipient, and uploads the ciphertext with a SHA-256 checksum.
The encrypted object and checksum sidecar receive the configured Object Lock
mode and retention date. Server-side encryption uses SSE-S3 by default or SSE-KMS
when `sseKmsKeyId` is configured.

Review CronJob and Job status, failed runs, bucket retention, and available
storage as part of routine operations. Retain the matching private `age` identity
under the organization's recovery controls; without it, client-side encrypted
backup contents cannot be read. A successful upload does not prove that a
separate restore procedure works.
