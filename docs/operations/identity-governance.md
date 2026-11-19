# Identity lifecycle and access reviews

DeployForge authenticates human users through OIDC. Enforce MFA, phishing-resistant authentication for administrators, conditional access, and IdP session policy at the identity provider. Production does not accept development passwords.

## OIDC group-to-role mapping

The OIDC UserInfo response may include a list of strings in the configured group claim (default `groups`). `DEPLOYFORGE_OIDC_GROUP_ROLE_MAPPINGS` maps each IdP group to workspace slugs and one of the four supported workspace roles:

```json
{
  "deployforge-platform-admins": {"production": "workspace_admin"},
  "deployforge-release-engineers": {"production": "platform_engineer"},
  "deployforge-auditors": {"production": "auditor"}
}
```

Configure the JSON as a protected, non-secret setting and verify that every workspace slug exists. If several groups grant a user roles in the same workspace, DeployForge selects the strongest mapped role. IdP-managed roles are tagged as such in the workspace member list and cannot be edited or removed through manual member actions. Removing a group from the claim removes its access on the next OIDC login; SCIM group changes update mapped access directly. Existing manually granted memberships are preserved.

## SCIM 2.0 provisioning

Enable `config.scim.enabled` in Helm and provide `scim_bearer_token` in the external runtime Secret. The token must be at least 32 characters; use a provider-generated random credential and rotate it with the IdP SCIM connector. The endpoint base is `https://<host>/scim/v2`.

Supported resources are `ServiceProviderConfig`, `Users`, and `Groups`. User and group resources support GET, POST, PUT, PATCH, and DELETE where applicable; list filters support exact `userName eq "..."` and `displayName eq "..."`, with `startIndex` and `count` pagination. Bulk requests and sorting are not supported. Provision a user before their first OIDC login and configure `DEPLOYFORGE_OIDC_SCIM_EXTERNAL_ID_CLAIM` to the OIDC claim whose value matches the SCIM `externalId` (the Helm default is `employeeNumber`). A verified OIDC login then links that pre-provisioned record to the IdP subject. An email match alone never links accounts.

SCIM `active=false` immediately revokes sessions and workspace memberships. Group memberships map through the same group-role mapping setting; use the exact SCIM group `displayName` as the mapping key. SCIM-provisioned users still authenticate through OIDC and must present a verified email. Monitor IdP provisioning errors, inactive accounts, and group-to-workspace mappings.

## Workspace access review

Workspace administrators can review current members and provenance using `GET /api/v1/workspaces/{workspace_id}/members`. The response identifies manual vs IdP-managed access and the granting groups. Record a retain/revoke decision with `POST /api/v1/workspaces/{workspace_id}/members/{user_id}/review` and a JSON body such as:

```json
{"decision": "retain", "comment": "Quarterly review, still on call for this service"}
```

The endpoint requires a CSRF-protected administrator session and rejects self-review. Manual access can be revoked by a review decision. For IdP-managed access, remove the user's mapped group in the IdP/SCIM source; DeployForge records the revocation request and denies pretending a local deletion would persist. Run and record reviews at least quarterly, and immediately after role, team, or employment changes.

## Emergency access

Keep two named emergency administrator identities in the IdP's protected recovery process, separate from routine accounts, with hardware security keys and audited custody. Add only those verified email addresses to `DEPLOYFORGE_OIDC_ADMIN_EMAILS`; never enable development login in production. Allowlist-managed platform administrator access is source-tracked and revoked on the next authenticated request or OIDC login after an address is removed from the setting. Alert on emergency-account use, remove the allowlist entry after the incident review, and verify the revocation audit event. DeployForge has no local password or IdP-bypass path in production: if the IdP itself is unavailable, restore IdP access through its break-glass procedure before signing in.
