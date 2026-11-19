import { useEffect, useId, useRef, useState } from "react";
import { ArrowUpRight, Eye, EyeOff, Fingerprint, LockKeyhole, ShieldCheck } from "lucide-react";
import { apiPost, type AuthConfig, type AuthSession } from "../lib/api";

type Props = {
  config: AuthConfig;
  errorCode?: string;
  onSignedIn: (session: AuthSession) => void;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api/v1";

export default function AuthGate({ config, errorCode, onSignedIn }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const errorRef = useRef<HTMLDivElement>(null);
  const pendingRequest = useRef(false);
  const fieldId = useId();

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  async function signInLocally(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pendingRequest.current) return;
    pendingRequest.current = true;
    setBusy(true);
    setPasswordVisible(false);
    setError("");
    try {
      const session = await apiPost<AuthSession>("/auth/password-login", {
        email: email.trim(),
        password,
      });
      setPassword("");
      setPasswordVisible(false);
      onSignedIn(session);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign-in failed. Please try again.");
    } finally {
      pendingRequest.current = false;
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
        <p className="auth-intro">{config.development_login_enabled ? "Enter your email and password to access your workspace." : "Use your organization identity to access your workspace."}</p>

        {errorCode && <div className="auth-message auth-message-error" role="alert">{errorCode === "account_disabled" ? "This account is disabled. Contact a platform administrator." : errorCode === "account_link" ? "This email is already linked to a different identity. Contact a platform administrator." : "Identity provider sign-in could not be completed. Try again or contact your administrator."}</div>}
        {error && <div ref={errorRef} className="auth-message auth-message-error" role="alert" tabIndex={-1}>{error}</div>}

        {config.development_login_enabled && (
            <form className="dev-login-form auth-credentials-form" onSubmit={signInLocally} aria-busy={busy}>
              <fieldset className="auth-credentials-fields" disabled={busy}>
                <label htmlFor={`${fieldId}-email`}>Email address<input id={`${fieldId}-email`} name="email" type="email" autoComplete="username" autoCapitalize="none" spellCheck={false} required maxLength={320} value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com" /></label>
                <div className="auth-password-field">
                  <label htmlFor={`${fieldId}-password`}>Password</label>
                  <div className="auth-password-control"><input id={`${fieldId}-password`} name="password" type={passwordVisible ? "text" : "password"} autoComplete="current-password" required maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Enter your password" /><button type="button" className="auth-password-toggle" aria-label={passwordVisible ? "Hide password" : "Show password"} aria-pressed={passwordVisible} aria-controls={`${fieldId}-password`} onClick={() => setPasswordVisible((visible) => !visible)}>{passwordVisible ? <EyeOff size={17} aria-hidden="true" /> : <Eye size={17} aria-hidden="true" />}</button></div>
                </div>
              </fieldset>
              <button className="auth-primary-button auth-sign-in" type="submit" disabled={busy}>
                <LockKeyhole size={16} aria-hidden="true" /> {busy ? "Signing in…" : "Sign in"} <ArrowUpRight size={16} aria-hidden="true" />
              </button>
              <p className="dev-login-note">Use your local DeployForge account. Contact your platform administrator if your password has not been set.</p>
            </form>
        )}

        {config.oidc_enabled && <>
          {config.development_login_enabled && <div className="auth-divider"><span>OR CONTINUE WITH</span></div>}
          <button className={config.development_login_enabled ? "auth-secondary-button" : "auth-primary-button"} disabled={busy} onClick={() => window.location.assign(`${API_BASE}/auth/login`)}>
            <Fingerprint size={17} /> Continue with organization SSO <ArrowUpRight size={15} />
          </button>
        </>}

        {!config.oidc_enabled && !config.development_login_enabled && (
          <div className="auth-message">No sign-in method is available. Configure the identity provider before opening the control plane.</div>
        )}
        <footer className="auth-footer"><span>Protected by workspace-scoped access controls</span><span>DeployForge Enterprise</span></footer>
      </section>
    </main>
  );
}
