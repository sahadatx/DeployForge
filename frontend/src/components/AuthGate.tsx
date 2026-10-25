import { useState } from "react";
import { ArrowUpRight, Fingerprint, ShieldCheck, TerminalSquare } from "lucide-react";
import { apiPost, type AuthConfig, type AuthSession } from "../lib/api";

type Props = {
  config: AuthConfig;
  errorCode?: string;
  onSignedIn: (session: AuthSession) => void;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api/v1";

export default function AuthGate({ config, errorCode, onSignedIn }: Props) {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function signInLocally(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const session = await apiPost<AuthSession>("/auth/dev-login", {
        email,
        display_name: displayName || undefined,
      });
      onSignedIn(session);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Development sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand">
          <div className="brand-mark" aria-hidden="true"><span>DF</span></div>
          <div className="brand-copy"><strong>DeployForge</strong><span>ENTERPRISE PLATFORM</span></div>
        </div>
        <div className="auth-eyebrow"><ShieldCheck size={14} /> CONTROL PLANE ACCESS</div>
        <h1 id="auth-title">Sign in to your platform</h1>
        <p className="auth-intro">Use your organization identity to access approved workspaces and platform operations.</p>

        {errorCode && <div className="auth-message auth-message-error" role="alert">{errorCode === "account_disabled" ? "This account is disabled. Contact a platform administrator." : errorCode === "account_link" ? "This email is already linked to a different identity. Contact a platform administrator." : "Identity provider sign-in could not be completed. Try again or contact your administrator."}</div>}
        {error && <div className="auth-message auth-message-error" role="alert">{error}</div>}

        {config.oidc_enabled ? (
          <button className="auth-primary-button" onClick={() => window.location.assign(`${API_BASE}/auth/login`)}>
            <Fingerprint size={17} /> Continue with organization SSO <ArrowUpRight size={15} />
          </button>
        ) : (
          <div className="auth-provider-unavailable"><Fingerprint size={17} /><span><strong>Organization SSO is not configured</strong><small>Set an OIDC discovery URL and client credentials to enable sign-in.</small></span></div>
        )}

        {config.development_login_enabled && (
          <>
            <div className="auth-divider"><span>LOCAL DEVELOPMENT</span></div>
            <form className="dev-login-form" onSubmit={signInLocally}>
              <label>Email address<input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="engineer@company.dev" /></label>
              <label>Display name <span className="field-optional">optional</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Platform Engineer" /></label>
              <button className="auth-secondary-button" type="submit" disabled={busy}>
                <TerminalSquare size={16} /> {busy ? "Signing in…" : "Continue with local account"}
              </button>
              <p className="dev-login-note">Local-only identity. The first development account can bootstrap the platform administrator when explicitly enabled.</p>
            </form>
          </>
        )}

        {!config.oidc_enabled && !config.development_login_enabled && (
          <div className="auth-message">No sign-in method is available. Configure the identity provider before opening the control plane.</div>
        )}
        <footer className="auth-footer"><span>Protected by workspace-scoped access controls</span><span>DeployForge Enterprise</span></footer>
      </section>
    </main>
  );
}
