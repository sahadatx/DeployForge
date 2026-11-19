import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Building2, CircleUserRound, KeyRound, Plus, ShieldCheck, Trash2, UsersRound } from "lucide-react";
import { PageFrame } from "../components/PageFrame";
import { FieldLabel, FormDrawer } from "../components/FormDrawer";
import { slugFromName } from "../lib/forms";
import { RecordDrawer, ViewAction } from "../components/RecordDrawer";
import { formatDate, TablePagination, TableToolbar, useTableView } from "../components/WorkspacePage";
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
  const [workspaceSlugEdited, setWorkspaceSlugEdited] = useState(false);
  const [workspaceCreateOpen, setWorkspaceCreateOpen] = useState(false);
  const [memberEmail, setMemberEmail] = useState("");
  const [memberRole, setMemberRole] = useState<Role>("platform_engineer");
  const [grantOpen, setGrantOpen] = useState(false);
  const [reviewMemberId, setReviewMemberId] = useState("");
  const [reviewDecision, setReviewDecision] = useState<"retain" | "revoke">("retain");
  const [reviewComment, setReviewComment] = useState("");
  const [selectedMemberId, setSelectedMemberId] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [compactDensity, setCompactDensity] = useState(() => localStorage.getItem("deployforge.density") === "compact");
  const [timeZone, setTimeZone] = useState(() => localStorage.getItem("deployforge.timezone") || "system");
  const settingsRequest = useRef(0);
  const activeWorkspace = useRef(workspaceId);
  activeWorkspace.current = workspaceId;
  const membersTable = useTableView(members, (member) => `${member.user.display_name} ${member.user.email} ${member.role}`, {
    resetKey: workspaceId,
    filters: [
      { id: "role", label: "Role", getValue: (member) => member.role, options: roles.map((role) => ({ value: role, label: role.replaceAll("_", " ") })) },
      { id: "account", label: "Account status", getValue: (member) => member.user.active ? "active" : "inactive", options: [{ value: "active", label: "Active" }, { value: "inactive", label: "Inactive" }] },
    ],
  });
  const selectedMember = members.find((member) => member.membership_id === selectedMemberId);
  const reviewMember = members.find((member) => member.membership_id === reviewMemberId);

  useEffect(() => {
    setSelectedMemberId("");
    setGrantOpen(false);
    setMemberEmail("");
    setMemberRole("platform_engineer");
    setWorkspace(null);
    setMembers([]);
  }, [workspaceId]);

  useEffect(() => {
    localStorage.setItem("deployforge.density", compactDensity ? "compact" : "comfortable");
    document.documentElement.dataset.density = compactDensity ? "compact" : "comfortable";
  }, [compactDensity]);

  useEffect(() => { localStorage.setItem("deployforge.timezone", timeZone); }, [timeZone]);

  const loadSettings = useCallback(async () => {
    if (workspaceId !== activeWorkspace.current) return;
    const requestId = ++settingsRequest.current;
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
      if (requestId !== settingsRequest.current || workspaceId !== activeWorkspace.current) return;
      setSession(current);
      setWorkspace(workspaceList.find((item) => item.id === workspaceId) || null);
      setMembers(memberList);
      setError("");
    } catch (caught) {
      if (requestId !== settingsRequest.current || workspaceId !== activeWorkspace.current) return;
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

  function resetWorkspaceForm() {
    setWorkspaceName(""); setWorkspaceSlug(""); setWorkspaceSlugEdited(false); setError("");
  }

  async function createWorkspace(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const created = await apiPost<Workspace>("/workspaces", { name: workspaceName.trim(), slug: workspaceSlug.trim() });
      localStorage.setItem("deployforge.workspace", created.id);
      window.location.assign("/settings");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Workspace could not be created.");
      setBusy(false);
    }
  }

  async function grantAccess(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await apiPost(`/workspaces/${workspaceId}/members`, { email: memberEmail.trim(), role: memberRole });
      setMemberEmail("");
      setMemberRole("platform_engineer");
      setGrantOpen(false);
      setNotice("Workspace access granted.");
      await loadSettings();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not grant workspace access.");
    } finally {
      setBusy(false);
    }
  }

  async function changeRole(member: WorkspaceMember, role: Role) {
    if (!workspaceId || busy) return;
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
    if (!workspaceId || busy || !window.confirm(`Revoke ${member.user.email}'s access to this workspace?`)) return;
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

  async function reviewAccess(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || !reviewMember || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await apiPost<{ status: string }>(
        `/workspaces/${workspaceId}/members/${reviewMember.user.id}/review`,
        { decision: reviewDecision, comment: reviewComment.trim() },
      );
      setReviewMemberId("");
      setReviewComment("");
      setNotice(result.status === "pending_external"
        ? `Review recorded. Remove ${reviewMember.user.display_name} from the mapped identity group to complete revocation.`
        : reviewDecision === "retain"
          ? `Access retained for ${reviewMember.user.display_name}.`
          : `Access revoked for ${reviewMember.user.display_name}.`);
      await loadSettings();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not record the access review.");
    } finally {
      setBusy(false);
    }
  }

  const isPlatformAdmin = Boolean(session?.user.is_platform_admin);
  const selectedRole = session?.memberships.find((membership) => membership.workspace_id === workspaceId)?.role;
  const canManageWorkspace = isPlatformAdmin || selectedRole === "workspace_admin";
  const showCreateWorkspace = isPlatformAdmin;

  return (
    <PageFrame eyebrow="GOVERNANCE" title="Settings" description="Manage workspace access, platform identity and control plane configuration.">
      {(error || notice) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || notice}</div>}

      {showCreateWorkspace && (
        <section className="settings-panel">
          <div className="settings-panel-heading"><div className="settings-panel-icon"><Building2 size={17} /></div><div><h2>{workspace ? "Create another workspace" : "Create your first workspace"}</h2><p>Workspaces scope service ownership, environment access and audit history.</p></div><button type="button" className="settings-primary-button settings-heading-action" disabled={busy} onClick={() => { resetWorkspaceForm(); setWorkspaceCreateOpen(true); }}><Plus size={15} /> Create workspace</button></div>
          <p className="settings-footnote">The current platform administrator will be added as the workspace administrator. Workspace creation is recorded in the audit log.</p>
        </section>
      )}

      {workspaceCreateOpen && isPlatformAdmin && <FormDrawer title="Create workspace" eyebrow="WORKSPACE SETUP" description="Create a boundary for service ownership, environments and audit history." busy={busy} error={error} dirty={Boolean(workspaceName || workspaceSlug)} onClose={() => { resetWorkspaceForm(); setWorkspaceCreateOpen(false); }} onReset={resetWorkspaceForm} actions={<button form="create-workspace-form" type="submit" className="settings-primary-button" disabled={busy}><Plus size={15} />{busy ? "Creating…" : "Create workspace"}</button>}>
        <form id="create-workspace-form" className="resource-form" onSubmit={createWorkspace}>
          <label><FieldLabel required>Workspace name</FieldLabel><input required minLength={2} maxLength={160} value={workspaceName} onChange={(event) => { setWorkspaceName(event.target.value); if (!workspaceSlugEdited) setWorkspaceSlug(slugFromName(event.target.value, 80)); }} placeholder="Production Platform" /></label>
          <label><FieldLabel required>Workspace slug</FieldLabel><input required pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" maxLength={80} value={workspaceSlug} onChange={(event) => { setWorkspaceSlug(event.target.value); setWorkspaceSlugEdited(true); }} placeholder="production-platform" aria-describedby="workspace-slug-hint" /></label>
          <p id="workspace-slug-hint" className="form-note form-wide">Use lowercase letters, numbers and hyphens. The workspace slug is its permanent identifier.</p>
        </form>
      </FormDrawer>}

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
        <div className="settings-panel-heading"><div className="settings-panel-icon"><UsersRound size={17} /></div><div><h2>Workspace members</h2><p>{workspace ? `${workspace.name} · Manage access for users who have signed in.` : "Select or create a workspace to manage its members."}</p></div>{workspace && canManageWorkspace && <button type="button" className="settings-primary-button settings-heading-action" onClick={() => { setError(""); setGrantOpen(true); }}><Plus size={15} /> Grant access</button>}</div>
        {workspace && canManageWorkspace ? (
          <>
            <p className="settings-footnote"><KeyRound size={13} /> Users must complete sign-in before they can be added. Workspace administrators can manage access; only platform administrators can grant that role.</p>
            <div className="member-table-wrap">
              {members.length > 0 && <TableToolbar query={membersTable.query} onQueryChange={membersTable.setQuery} filteredCount={membersTable.filteredItems.length} totalCount={membersTable.totalItems} pageSize={membersTable.pageSize} onPageSizeChange={membersTable.setPageSize} filters={membersTable.filters} onFilterChange={membersTable.setFilterValue} onResetFilters={membersTable.resetFilters} isFiltering={membersTable.isFiltering} label="workspace members" />}
              {members.length > 0 && membersTable.filteredItems.length === 0 && <div className="settings-empty">No members match this filter.</div>}
              <table className="member-table has-actions"><caption className="sr-only">Workspace members, role, access source, review date and actions</caption><thead><tr><th scope="col">Member</th><th scope="col">Role</th><th scope="col">Access source</th><th scope="col">Access since</th><th scope="col">Actions</th></tr></thead>
                <tbody>{membersTable.pageItems.map((member) => <tr key={member.membership_id}><td><div className="member-identity"><span className="member-avatar">{member.user.display_name.slice(0, 1).toUpperCase()}</span><span><strong>{member.user.display_name}</strong><small>{member.user.email}</small></span></div></td><td><select aria-label={`Role for ${member.user.email}`} disabled={busy || member.identity_source !== "manual" || (!isPlatformAdmin && member.role === "workspace_admin")} value={member.role} onChange={(event) => void changeRole(member, event.target.value as Role)}>{roles.filter((role) => role !== "workspace_admin" || isPlatformAdmin || member.role === role).map((role) => <option key={role} value={role}>{role.replaceAll("_", " ")}</option>)}</select></td><td>{member.identity_source === "idp" ? <span title={member.identity_groups?.join(", ")}>Identity provider{member.identity_groups?.length ? ` · ${member.identity_groups.slice(0, 2).join(", ")}` : ""}</span> : member.identity_source === "scim" ? <span title={member.identity_groups?.join(", ")}>SCIM provisioning{member.identity_groups?.length ? ` · ${member.identity_groups.slice(0, 2).join(", ")}` : ""}</span> : "Manual"}</td><td>{formatDate(member.created_at)}</td><td><div className="member-actions"><ViewAction label={member.user.display_name} onClick={() => setSelectedMemberId(member.membership_id)} /><button type="button" className="catalog-secondary-button" disabled={busy || member.user.id === session?.user.id} onClick={() => { setReviewMemberId(member.membership_id); setReviewDecision("retain"); setReviewComment(""); setError(""); }} aria-label={`Review ${member.user.email} access`}>Review</button><button className="icon-button danger-icon" disabled={busy || member.identity_source !== "manual"} onClick={() => void revokeAccess(member)} aria-label={`Revoke ${member.user.email}`} title={member.identity_source === "manual" ? "Revoke workspace access" : "Remove the mapped group to revoke managed access"}><Trash2 size={15} /></button></div></td></tr>)}</tbody>
              </table>
              {members.length > 0 && <TablePagination firstItem={membersTable.firstItem} lastItem={membersTable.lastItem} totalItems={membersTable.filteredItems.length} page={membersTable.page} pageCount={membersTable.pageCount} onPageChange={membersTable.setPage} label="workspace members" />}
              {members.length === 0 && <div className="settings-empty">No workspace members are recorded yet.</div>}
            </div>
          </>
        ) : (
          <div className="settings-empty">{workspace ? "Workspace administrator permission is required to manage members." : "No workspace is selected."}</div>
        )}
      </section>

      {grantOpen && workspace && canManageWorkspace && <FormDrawer title="Grant workspace access" eyebrow="WORKSPACE MEMBERS" description={`Add an existing account to ${workspace.name} with the permissions needed for their work.`} busy={busy} error={error} dirty={Boolean(memberEmail || memberRole !== "platform_engineer")} onClose={() => { setGrantOpen(false); setMemberEmail(""); setMemberRole("platform_engineer"); }} onReset={() => { setMemberEmail(""); setMemberRole("platform_engineer"); }} actions={<button form="grant-workspace-access" type="submit" className="settings-primary-button" disabled={busy}><Plus size={15} /> Grant access</button>}>
        <form id="grant-workspace-access" className="catalog-form" onSubmit={grantAccess}><div className="catalog-form-grid">
          <label className="catalog-form-wide"><FieldLabel required>User email</FieldLabel><input type="email" required maxLength={254} value={memberEmail} onChange={(event) => setMemberEmail(event.target.value)} placeholder="engineer@company.com" /></label>
          <label className="catalog-form-wide"><FieldLabel required>Workspace role</FieldLabel><select required value={memberRole} onChange={(event) => setMemberRole(event.target.value as Role)}>{roles.filter((role) => role !== "workspace_admin" || isPlatformAdmin).map((role) => <option key={role} value={role}>{role.replaceAll("_", " ")}</option>)}</select></label>
        </div><p className="settings-footnote form-note"><KeyRound size={13} /> The user must sign in before access can be granted. Only platform administrators can assign the workspace administrator role.</p></form>
      </FormDrawer>}
      {reviewMember && workspace && canManageWorkspace && <FormDrawer title="Review workspace access" eyebrow="ACCESS GOVERNANCE" description="Record an independent retain or revoke decision. The decision and rationale are written to the audit log." busy={busy} error={error} dirty={Boolean(reviewComment || reviewDecision !== "retain")} onClose={() => { setReviewMemberId(""); setReviewComment(""); }} onReset={() => { setReviewDecision("retain"); setReviewComment(""); }} actions={<button form="workspace-access-review" type="submit" className="settings-primary-button" disabled={busy || reviewComment.trim().length < 3}><ShieldCheck size={15} /> Record review</button>}>
        <form id="workspace-access-review" className="resource-form" onSubmit={reviewAccess}>
          <div className="form-wide"><strong>{reviewMember.user.display_name}</strong><p className="form-note">{reviewMember.user.email} · {reviewMember.role.replaceAll("_", " ")} · {reviewMember.identity_source === "idp" ? "Identity provider" : reviewMember.identity_source === "scim" ? "SCIM provisioning" : "Manual access"}</p></div>
          <label><FieldLabel required>Decision</FieldLabel><select value={reviewDecision} onChange={(event) => setReviewDecision(event.target.value as "retain" | "revoke")}><option value="retain">Retain access</option><option value="revoke">Revoke access</option></select></label>
          <label className="form-wide"><FieldLabel required>Review rationale</FieldLabel><textarea required minLength={3} maxLength={500} value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} placeholder="Why is this access still required, or why should it be removed?" /></label>
          {reviewDecision === "revoke" && reviewMember.identity_source !== "manual" && <p className="form-note form-wide">This access is managed by an identity group. DeployForge records the review decision; remove the user from the mapped group in the identity provider to complete revocation.</p>}
        </form>
      </FormDrawer>}
      {selectedMember && workspace && <RecordDrawer onClose={() => setSelectedMemberId("")} record={{ title: selectedMember.user.display_name, eyebrow: "WORKSPACE MEMBER", description: selectedMember.user.email, sections: [
        { title: "Workspace access", fields: [{ label: "Workspace", value: workspace.name }, { label: "Role", value: selectedMember.role.replaceAll("_", " ") }, { label: "Access since", value: formatDate(selectedMember.created_at) }, { label: "Membership ID", value: selectedMember.membership_id, mono: true }] },
        { title: "Account", fields: [{ label: "Account status", value: selectedMember.user.active ? "Active" : "Inactive" }, { label: "Email verification", value: selectedMember.user.email_verified ? "Verified" : "Unverified" }, { label: "Platform administrator", value: selectedMember.user.is_platform_admin ? "Yes" : "No" }, { label: "Account created", value: formatDate(selectedMember.user.created_at) }, { label: "User ID", value: selectedMember.user.id, mono: true }] },
      ] }} />}

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
