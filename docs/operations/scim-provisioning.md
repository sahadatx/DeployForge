# SCIM 2.0 provisioning

DeployForge exposes SCIM 2.0 at `/scim/v2`. Enable it only after configuring a strong bearer token in the API runtime. `DEPLOYFORGE_SCIM_BEARER_TOKEN` must contain at least 32 characters; keep it in the deployment secret manager and rotate it with the IdP connector. The endpoint returns `503` while no token is configured and `401` for missing or invalid bearer credentials.

The implementation supports `ServiceProviderConfig`, `Users`, and `Groups`. User and group list operations support one exact equality filter (`userName eq "..."` or `displayName eq "..."`) and SCIM `startIndex`/`count` pagination, with a maximum page size of 100. Bulk and sort operations are not supported. The deployment proxy routes `/scim/v2/` to the API.

Provision users before their first OIDC sign-in. The SCIM `externalId` is stored as a unique link key. Configure `DEPLOYFORGE_OIDC_SCIM_EXTERNAL_ID_CLAIM` to the UserInfo claim that contains the same value. A verified OIDC login links the pre-provisioned record by that external ID; a matching email alone never links accounts. SCIM-provisioned users cannot authenticate with a local password.

SCIM group `displayName` values use the same case-insensitive group-to-workspace mapping configured by `DEPLOYFORGE_OIDC_GROUP_ROLE_MAPPINGS`. Group additions, removals, renames, and deletions reconcile SCIM-sourced workspace roles immediately. If more than one mapped group grants a role in a workspace, the strongest role applies. SCIM-managed memberships are source-tagged, audited, and protected from manual edits. Existing manual memberships remain separate and are preserved while a group mapping is synchronized.

Setting a SCIM user inactive or deleting that user revokes their active sessions and all workspace memberships. Reactivating the user restores access only from current mapped SCIM groups. Do not use this endpoint for ordinary temporary leave unless all access should be revoked.

For Helm deployments, set `config.scim.enabled: true`, provide the key named by `secrets.keys.scimBearerToken` in the referenced runtime Secret, and set `config.oidc.scimExternalIdClaim` to the provider's matching claim. Then configure the IdP SCIM base URL as `https://<deployforge-host>/scim/v2` and provision a test user/group before enabling bulk synchronization.
