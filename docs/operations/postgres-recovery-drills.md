# PostgreSQL PITR and recovery drills

Use this runbook to recover DeployForge into a new, isolated database and to
record measured recovery objectives. A highly available database protects
against some node failures; it does not replace point-in-time recovery (PITR),
independent backups, or restore drills.

## Recovery objectives and readiness

Set the service owner's approved objectives before production. Suggested initial
targets for planning are **RPO 15 minutes** and **RTO 4 hours**; these are targets
to validate, not guarantees made by the application. Record measured results in
each drill. The off-site full dump runs every six hours by default, so it cannot
meet a 15-minute RPO on its own.

Configure the managed PostgreSQL service to continuously retain the transaction
logs and automated backups needed for PITR. Choose a retention window long enough
to cover detection and response time, and verify it against the provider's
restore controls. Keep the separately encrypted off-site dumps for a recovery
path independent of the database service. A standby or read replica is not a
PITR copy because unwanted writes can be replicated.

Before a drill, confirm:

- A recent managed-database restore point is available and the selected target
  time is inside the provider's PITR retention window.
- The most recent encrypted dump and its `.sha256` sidecar are present in the
  off-site bucket, with the expected Object Lock retention.
- The private `age` identity is retrievable through the approved secret manager
  and its matching public recipient is known.
- A separate restore instance or database has adequate storage, compatible
  PostgreSQL major version, required extensions, network access, and TLS CA.
- The recovery operator can access the service health endpoint and the approved
  application smoke checks.

## Managed-service PITR drill

Run at least monthly in a non-production recovery environment, and include a
different region or account in a scheduled cross-region drill. Use the managed
database provider's PITR workflow to create a **new** instance at a timestamp
just before the selected simulated incident. Do not restore over the active
instance.

1. Record the simulated incident time and chosen recovery timestamp. For a real
   incident, coordinate any write freeze or traffic change through the incident
   lead before acting.
2. Ask the provider to create a new database instance from the selected point.
   Preserve the original database and its logs while validating the recovery.
3. Match the source PostgreSQL major version, extensions, collation, parameter
   settings, encryption, and network/TLS policy. Create the application database
   and least-privilege role according to the database runbook.
4. Point a non-production DeployForge instance at the recovered database. Check
   migration state, key row counts, workspace and service reads, a recent release
   record, and the authenticated health/read path.
5. Measure RTO from drill start until those checks pass. Measure RPO from the
   last known committed event before the incident to the latest event present in
   the recovered database. Save the provider recovery point and evidence.
6. Delete the drill instance only after evidence is recorded and the owner
   approves cleanup. Never redirect production traffic as part of a drill.

## Restore an encrypted off-site dump

Use this path when testing the independent dump or when provider PITR is
unavailable. Run it from a restricted recovery host with encrypted temporary
storage. The restore script needs Bash, `age`, GNU `sha256sum`, and PostgreSQL
client tools compatible with the dump's PostgreSQL major version. Keep the
database URLs and private age identity in a protected process environment or
secret manager; do not put credentials in shell history or command arguments.

Download the encrypted object and its checksum sidecar, keeping the original
object basename so the sidecar can verify it:

```sh
umask 077
mkdir -m 700 "$RECOVERY_DIR"
backup_name=${S3_KEY##*/}
aws s3 cp "s3://$S3_BUCKET/$S3_KEY" "$RECOVERY_DIR/$backup_name"
aws s3 cp "s3://$S3_BUCKET/$S3_KEY.sha256" "$RECOVERY_DIR/$backup_name.sha256"
```

Provide these values through the protected recovery environment:

- `DEPLOYFORGE_ACTIVE_DATABASE_URL`: the current production database URL, used
  only for the destination safety check.
- `DEPLOYFORGE_RESTORE_DATABASE_URL`: a URL for a **new, empty** database. The
  script refuses the same host, port, and database name as the active URL.
- `DEPLOYFORGE_DATABASE_SSLMODE` and `DEPLOYFORGE_DATABASE_SSLROOTCERT`: the
  required TLS mode and CA path for both connections, such as `verify-full` and
  the mounted provider CA file.
- `AGE_IDENTITY_FILE`: a protected file containing the private identity that
  matches the configured backup recipient.

Then run:

```sh
scripts/restore-postgres-offsite.sh "$RECOVERY_DIR/$backup_name"
```

The script verifies the adjacent SHA-256 sidecar before decrypting, validates
the dump, refuses the exact active database endpoint and any destination that
already contains application relations, restores in one transaction, and runs a
connection check. A failed restore leaves the new database unchanged; investigate
the error and create another empty target before retrying. The script never
switches application traffic.

Run the same non-production application checks listed in the PITR drill before
considering a recovered database usable. Keep the decrypted dump on encrypted
temporary storage and confirm cleanup after the drill.

## Drill record

Keep one record per drill with the following fields:

| Field | Record |
| --- | --- |
| Drill ID, operator, reviewer |  |
| Scenario and recovery path | Managed PITR or encrypted off-site dump |
| Incident time and target recovery time |  |
| Source backup/object version and checksum, if applicable |  |
| Drill start and service-validation completion times |  |
| Observed RPO and RTO |  |
| Database version, extensions, and recovered instance/region |  |
| Validation evidence and failed checks |  |
| Follow-up actions, owner, and due date |  |

Investigate any missed objective or failed restore, assign corrective work, and
repeat the drill after the recovery path changes.
