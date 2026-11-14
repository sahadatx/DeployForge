# Emergency administrator access

DeployForge uses the organization's OIDC provider for human sign-in. Emergency access uses named, protected IdP accounts; it does not create a local password or an IdP bypass.

## Prepare

1. Keep two named emergency administrator accounts in the IdP's recovery process, separate from routine accounts.
2. Require phishing-resistant MFA and store recovery material under audited custody.
3. Add only the emergency accounts' verified email addresses to `DEPLOYFORGE_OIDC_ADMIN_EMAILS` through the protected deployment configuration. Keep this list small and review it quarterly.
4. Confirm the OIDC provider returns `email_verified=true` for these accounts. DeployForge grants platform administrator access only after a verified OIDC login.

## Use and revoke

An allowlisted login is recorded as `authentication.login.succeeded`; platform administrator grants and revocations are recorded as `identity.platform_admin.allowlist_granted` and `identity.platform_admin.allowlist_revoked`. The audit events identify the affected account and allowlist source.

For an emergency, use the named IdP account and record the incident reference in the organization's incident process. After the event, remove that email from `DEPLOYFORGE_OIDC_ADMIN_EMAILS`, deploy the configuration change, then make an authenticated request for the account or complete a new OIDC login so DeployForge reconciles and audits the revocation. Confirm that the account no longer has platform administrator access.

If the IdP is unavailable, restore IdP access through its own recovery process before signing in to DeployForge. DeployForge has no production local-password or identity-provider bypass path.
