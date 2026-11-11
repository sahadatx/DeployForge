# OIDC group to workspace role mapping

DeployForge can provision workspace memberships from an OIDC group claim during each successful sign-in. Configure the claim name and JSON map in the API environment:

```dotenv
DEPLOYFORGE_OIDC_GROUP_CLAIM=groups
DEPLOYFORGE_OIDC_GROUP_ROLE_MAPPINGS={"platform-engineering":{"platform":"platform_engineer"},"release-admins":{"platform":"workspace_admin","payments":"service_owner"}}
```

Each mapping key is an identity-provider group name. Each value maps an existing DeployForge workspace slug to one supported role: `workspace_admin`, `platform_engineer`, `service_owner`, or `auditor`. Workspace slugs must already exist. Group names are matched without regard to case. Invalid JSON, duplicate case-insensitive group names, unknown roles, and invalid slugs prevent the API from starting.

The identity provider must include the configured group claim in its user information response. When mappings are configured, a missing group claim rejects sign-in rather than silently preserving stale access. A valid empty group list removes prior IdP-managed memberships. Group membership changes take effect on the next sign-in.

If multiple mapped groups grant different roles to the same workspace, DeployForge applies the most privileged mapped role. IdP-managed memberships record their source and matching groups in the member list and audit log. Workspace administrators cannot edit or revoke those memberships manually; access changes must be made in the identity provider. Manually granted memberships are preserved by synchronization.

Keep this mapping in deployment configuration and restrict who can change it. For Helm, configure `config.oidc.groupClaim` and `config.oidc.groupRoleMappings` in chart values.
