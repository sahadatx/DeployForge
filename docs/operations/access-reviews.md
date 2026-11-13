# Workspace access reviews

Workspace roles and reviews are scoped to one workspace. Members can view that workspace's access list; granting, changing, or reviewing access requires a workspace administrator. Only a platform administrator can assign the workspace administrator role. Operational roles remain separate from membership administration.

An access review lets an independent administrator record whether a member still needs their current role. A reviewer cannot review their own membership.

Open **Settings → Workspace members → Review** for the member. Choose **Retain access** or **Revoke access** and enter a short rationale. DeployForge records the reviewer, target membership, role, access source, decision, and rationale in the audit log. Review decisions require at least three characters of rationale.

For a manual membership, a revoke decision removes workspace access immediately. For OIDC or SCIM managed access, DeployForge records the decision as pending external action and leaves role changes to the identity source. Remove the user from the mapped identity group; the next group synchronization applies the revocation. Manual role edits and direct removal are disabled for identity-managed memberships.

Run a review at least quarterly and after team, role, or employment changes. Review the member list and its access source before deciding. Keep review records in the audit retention workflow used by your organization.
