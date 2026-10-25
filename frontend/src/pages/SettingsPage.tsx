import { useCallback, useEffect, useState } from "react";
import { BookOpen, Building2, CircleUserRound, KeyRound, Plus, ShieldCheck, Trash2, UsersRound } from "lucide-react";
import { PageFrame } from "../components/PageFrame";
import { apiDelete, apiGet, apiPatch, apiPost, type AuthConfig, type AuthSession, type Workspace, type WorkspaceMember } from "../lib/api";

const roles = ["workspace_admin", "platform_engineer", "service_owner", "auditor"] as const;
type Role = (typeof roles)[number];

export default function SettingsPage() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null);
  const [workspaceId, setWorkspaceId] = useState(() => localStorage.getItem("deployforge.workspace") || "");
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceSlug, setWorkspaceSlug] = useState("");
  const [memberEmail, setMemberEmail] = useState("");
  const [memberRole, setMemberRole] = useState<Role>("platform_engineer");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [compactDensity, setCompactDensity] = useState(() => localStorage.getItem("deployforge.density") === "compact");
  const [timeZone, setTimeZone] = useState(() => localStorage.getItem("deployforge.timezone") || "system");

  useEffect(() => {
    localStorage.setItem("deployforge.density", compactDensity ? "compact" : "comfortable");
    document.documentElement.dataset.density = compactDensity ? "compact" : "comfortable";
  }, [compactDensity]);

  useEffect(() => { localStorage.setItem("deployforge.timezone", timeZone); }, [timeZone]);

  const loadSettings = useCallback(async () => {
    if (!workspaceId) {
      setWorkspace(null);
      setMembers([]);
      return;
    }
    try {
      const [current, workspaceList, memberList] = await Promise.all([
        apiGet<AuthSession>("/auth/me"),
        apiGet<Workspace[]>("/workspaces"),
        apiGet<WorkspaceMember[]>(`/workspaces/${workspaceId}/members`),
      ]);
      setSession(current);
      setWorkspace(workspaceList.find((item) => item.id === workspaceId) || null);
      setMembers(memberList);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load access settings.");
    }
  }, [workspaceId]);

  useEffect(() => {
    void apiGet<AuthSession>("/auth/me").then(setSession).catch(() => undefined);
    void apiGet<AuthConfig>("/auth/config").then(setAuthConfig).catch(() => undefined);
  }, []);

  useEffect(() => {
    const updateWorkspace = () => setWorkspaceId(localStorage.getItem("deployforge.workspace") || "");
    window.addEventListener("deployforge:workspace-changed", updateWorkspace);
    window.addEventListener("storage", updateWorkspace);
    return () => {
      window.removeEventListener("deployforge:workspace-changed", updateWorkspace);
      window.removeEventListener("storage", updateWorkspace);
    };
  }, []);

  useEffect(() => { void loadSettings(); }, [loadSettings]);

  useEffect(() => {
    const title = workspaceName.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    setWorkspaceSlug(title.slice(0, 80));
  }, [workspaceName]);

  async function createWorkspace(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const created = await apiPost<Workspace>("/workspaces", { name: workspaceName, slug: workspaceSlug });
      localStorage.setItem("deployforge.workspace", created.id);
      window.location.assign("/settings");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Workspace could not be created.");
      setBusy(false);
    }
  }

  async function grantAccess(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await apiPost(`/workspaces/${workspaceId}/members`, { email: memberEmail, role: memberRole });
      setMemberEmail("");
      setNotice("Workspace access granted.");
      await loadSettings();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not grant workspace access.");
    } finally {
      setBusy(false);
    }
  }

  async function changeRole(member: WorkspaceMember, role: Role) {
    if (!workspaceId) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await apiPatch(`/workspaces/${workspaceId}/members/${member.user.id}`, { role });
      setNotice(`Role updated for ${member.user.email}.`);
      await loadSettings();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not update the role.");
      await loadSettings();
    } finally {
      setBusy(false);
    }
  }

  async function revokeAccess(member: WorkspaceMember) {
    if (!workspaceId || !window.confirm(`Revoke ${member.user.email}'s access to this workspace?`)) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await apiDelete(`/workspaces/${workspaceId}/members/${member.user.id}`);
      setNotice(`Workspace access revoked for ${member.user.email}.`);
      await loadSettings();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not revoke workspace access.");
    } finally {
      setBusy(false);
    }
  }

  const isPlatformAdmin = Boolean(session?.user.is_platform_admin);
  const selectedRole = session?.memberships.find((membership) => membership.workspace_id === workspaceId)?.role;
  const canManageWorkspace = isPlatformAdmin || selectedRole === "workspace_admin";
  const showCreateWorkspace = isPlatformAdmin && (!workspaceId || !workspace);

  return (
    <PageFrame eyebrow="GOVERNANCE" title="Settings" description="Manage workspace access, platform identity and control plane configuration.">
      {(error || notice) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || notice}</div>}

      {showCreateWorkspace && (
        <section className="settings-panel">
          <div className="settings-panel-heading"><div className="settings-panel-icon"><Building2 size={17} /></div><div><h2>Create your first workspace</h2><p>Workspaces scope service ownership, environment access and audit history.</p></div></div>
          <form className="settings-form settings-create-workspace" onSubmit={createWorkspace}>
            <label>Workspace name<input required minLength={2} maxLength={160} value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} placeholder="Production Platform" /></label>
            <label>Workspace slug<input required pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" maxLength={80} value={workspaceSlug} onChange={(event) => setWorkspaceSlug(event.target.value)} placeholder="production-platform" /></label>
            <button className="settings-primary-button" disabled={busy || !workspaceSlug}><Plus size={15} /> Create workspace</button>
          </form>
          <p className="settings-footnote">The current platform administrator will be added as the workspace administrator. Workspace creation is recorded in the audit log.</p>
        </section>
      )}

      <section className="settings-panel identity-panel">
        <div className="settings-panel-heading"><div className="settings-panel-icon"><CircleUserRound size={17} /></div><div><h2>Signed-in identity</h2><p>Your authenticated DeployForge account and platform-level permissions.</p></div></div>
        <div className="identity-summary">
          <div className="identity-avatar">{session?.user.display_name.split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "—"}</div>
          <div><strong>{session?.user.display_name || "Loading identity…"}</strong><span>{session?.user.email || ""}</span></div>
          <span className={`role-pill ${isPlatformAdmin ? "role-admin" : ""}`}><ShieldCheck size={13} />{isPlatformAdmin ? "Platform administrator" : "Workspace-scoped access"}</span>
        </div>
      </section>

      <section className="settings-panel"><div className="settings-panel-heading"><div className="settings-panel-icon"><CircleUserRound size={17} /></div><div><h2>Interface preferences</h2><p>Preferences are stored in this browser and apply to your DeployForge session.</p></div></div>
        <div className="preference-grid"><label className="preference-control"><span><strong>Compact data tables</strong><small>Show more operational rows on each screen.</small></span><input type="checkbox" checked={compactDensity} onChange={(event) => setCompactDensity(event.target.checked)} /><span className="toggle-track" /></label><label className="preference-control"><span><strong>Time zone</strong><small>Used for dates throughout the dashboard.</small></span><select value={timeZone} onChange={(event) => setTimeZone(event.target.value)}><option value="system">System default</option><option value="UTC">UTC</option><option value="Asia/Dhaka">Asia/Dhaka</option></select></label><a className="documentation-link" href="/docs" target="_blank" rel="noreferrer"><BookOpen size={15} /><span><strong>API documentation</strong><small>Review available control plane endpoints and request contracts.</small></span><span>↗</span></a></div>
      </section>

      <section className="settings-panel">
        <div className="settings-panel-heading"><div className="settings-panel-icon"><UsersRound size={17} /></div><div><h2>Workspace members</h2><p>{workspace ? `${workspace.name} · Manage access for users who have signed in.` : "Select or create a workspace to manage its members."}</p></div></div>
        {workspace && canManageWorkspace ? (
          <>
            <form className="settings-form grant-access-form" onSubmit={grantAccess}>
              <label>User email<input type="email" required value={memberEmail} onChange={(event) => setMemberEmail(event.target.value)} placeholder="engineer@company.com" /></label>
              <label>Workspace role<select value={memberRole} onChange={(event) => setMemberRole(event.target.value as Role)}>{roles.filter((role) => role !== "workspace_admin" || isPlatformAdmin).map((role) => <option key={role} value={role}>{role.replaceAll("_", " ")}</option>)}</select></label>
              <button className="settings-primary-button" disabled={busy}><Plus size={15} /> Grant access</button>
            </form>
            <p className="settings-footnote"><KeyRound size={13} /> Users must complete sign-in before they can be added. Workspace administrators can manage access; only platform administrators can grant that role.</p>
            <div className="member-table-wrap">
              <table className="member-table"><thead><tr><th>Member</th><th>Role</th><th>Access since</th><th><span className="sr-only">Actions</span></th></tr></thead>
                <tbody>{members.map((member) => <tr key={member.membership_id}><td><div className="member-identity"><span className="member-avatar">{member.user.display_name.slice(0, 1).toUpperCase()}</span><span><strong>{member.user.display_name}</strong><small>{member.user.email}</small></span></div></td><td><select aria-label={`Role for ${member.user.email}`} disabled={busy || (!isPlatformAdmin && member.role === "workspace_admin")} value={member.role} onChange={(event) => void changeRole(member, event.target.value as Role)}>{roles.filter((role) => role !== "workspace_admin" || isPlatformAdmin || member.role === role).map((role) => <option key={role} value={role}>{role.replaceAll("_", " ")}</option>)}</select></td><td>{new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(member.created_at))}</td><td className="member-actions"><button className="icon-button danger-icon" disabled={busy} onClick={() => void revokeAccess(member)} aria-label={`Revoke ${member.user.email}`} title="Revoke workspace access"><Trash2 size={15} /></button></td></tr>)}</tbody>
              </table>
              {members.length === 0 && <div className="settings-empty">No workspace members are recorded yet.</div>}
            </div>
          </>
        ) : (
          <div className="settings-empty">{workspace ? "Workspace administrator permission is required to manage members." : "No workspace is selected."}</div>
        )}
      </section>

      <section className="settings-panel settings-placeholder">
        <div className="settings-panel-heading"><div className="settings-panel-icon"><KeyRound size={17} /></div><div><h2>Authentication configuration</h2><p>Sign-in capability is read from the live API configuration. Provider secrets remain server-side.</p></div></div>
        <div className="auth-config-grid"><ConfigValue label="Organization SSO" value={authConfig ? authConfig.oidc_enabled ? "Enabled" : "Not configured" : "Checking"} healthy={Boolean(authConfig?.oidc_enabled)} /><ConfigValue label="Local development login" value={authConfig ? authConfig.development_login_enabled ? "Enabled" : "Disabled" : "Checking"} healthy={Boolean(authConfig?.development_login_enabled)} /><ConfigValue label="Authentication required" value={authConfig ? authConfig.authentication_required ? "Yes" : "No" : "Checking"} healthy={Boolean(authConfig?.authentication_required)} /></div>
        <span className="settings-readonly-label">Configuration managed by platform operators · <a href="/docs" target="_blank" rel="noreferrer">API reference ↗</a></span>
      </section>
    </PageFrame>
  );
}

function ConfigValue({ label, value, healthy }: { label: string; value: string; healthy: boolean }) {
  return <div className="auth-config-value"><span className={`status-dot ${healthy ? "config-healthy" : ""}`} /><span><small>{label}</small><strong>{value}</strong></span></div>;
}
