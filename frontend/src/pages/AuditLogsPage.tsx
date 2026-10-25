import { useCallback, useEffect, useState } from "react";
import { Activity, FileClock, ShieldCheck } from "lucide-react";
import { PageFrame } from "../components/PageFrame";
import { apiGet, type AuditEvent, type AuthSession } from "../lib/api";

function describeAction(action: string) {
  return action.split(".").map((part) => part.replaceAll("_", " ")).join(" · ");
}

function describeDetails(details: Record<string, unknown>) {
  return Object.entries(details)
    .filter(([key]) => key !== "actor_email" && key !== "actor_name")
    .map(([key, value]) => `${key.replaceAll("_", " ")}: ${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join(" · ");
}

function shortId(value: string | null) {
  return value ? `${value.slice(0, 8)}…` : "—";
}

export default function AuditLogsPage() {
  const [workspaceId, setWorkspaceId] = useState(() => localStorage.getItem("deployforge.workspace") || "");
  const [isPlatformAdmin, setIsPlatformAdmin] = useState(false);
  const [identityLoaded, setIdentityLoaded] = useState(false);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const updateWorkspace = (event?: Event) => {
      if (event && "detail" in event) setWorkspaceId((event as CustomEvent<string>).detail || "");
      else setWorkspaceId(localStorage.getItem("deployforge.workspace") || "");
    };
    window.addEventListener("deployforge:workspace-changed", updateWorkspace);
    window.addEventListener("storage", updateWorkspace);
    return () => {
      window.removeEventListener("deployforge:workspace-changed", updateWorkspace);
      window.removeEventListener("storage", updateWorkspace);
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    apiGet<AuthSession>("/auth/me", controller.signal)
      .then((session) => setIsPlatformAdmin(session.user.is_platform_admin))
      .catch((caught) => {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) setError("Could not verify your audit access.");
      })
      .finally(() => setIdentityLoaded(true));
    return () => controller.abort();
  }, []);

  const loadEvents = useCallback(async (signal?: AbortSignal) => {
    if (!identityLoaded || (!isPlatformAdmin && !workspaceId)) {
      setEvents([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    const query = new URLSearchParams({ limit: "100" });
    if (workspaceId) query.set("workspace_id", workspaceId);
    try {
      setEvents(await apiGet<AuditEvent[]>(`/audit-logs?${query.toString()}`, signal));
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === "AbortError")) {
        setError(caught instanceof Error ? caught.message : "Audit history could not be loaded.");
      }
    } finally {
      setLoading(false);
    }
  }, [identityLoaded, isPlatformAdmin, workspaceId]);

  useEffect(() => {
    const controller = new AbortController();
    void loadEvents(controller.signal);
    return () => controller.abort();
  }, [loadEvents]);

  return (
    <PageFrame eyebrow="GOVERNANCE" title="Audit Logs" description="Review authenticated activity, access decisions and privileged workspace changes.">
      <section className="audit-summary-row">
        <div className="audit-summary-card"><span className="audit-summary-icon"><FileClock size={16} /></span><span><strong>{events.length}</strong><small>Recent events</small></span></div>
        <div className="audit-summary-card"><span className="audit-summary-icon"><ShieldCheck size={16} /></span><span><strong>{isPlatformAdmin ? "Platform" : "Workspace"}</strong><small>Access scope</small></span></div>
        <div className="audit-summary-card"><span className="audit-summary-icon"><Activity size={16} /></span><span><strong>Live records</strong><small>Read from the audit store</small></span></div>
      </section>
      <section className="settings-panel audit-panel">
        <div className="settings-panel-heading"><div className="settings-panel-icon"><FileClock size={17} /></div><div><h2>Recent activity</h2><p>{workspaceId ? "Events scoped to the selected workspace." : "Platform-wide events visible to platform administrators."} Showing up to 100 newest records.</p></div></div>
        {error && <div className="settings-notice is-error audit-error" role="alert">{error}</div>}
        {loading ? <div className="settings-empty">Loading audit history…</div> : !isPlatformAdmin && !workspaceId ? <div className="settings-empty">Select a workspace to view its audit history.</div> : events.length === 0 ? <div className="settings-empty">No audit events have been recorded for this scope yet.</div> : (
          <div className="member-table-wrap">
            <table className="audit-table"><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Resource</th><th>Result</th></tr></thead>
              <tbody>{events.map((event) => {
                const actorName = typeof event.details.actor_name === "string" ? event.details.actor_name : event.actor_id || "System";
                const actorEmail = typeof event.details.actor_email === "string" ? event.details.actor_email : "";
                return <tr key={event.id}>
                  <td><time dateTime={event.created_at}>{new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(event.created_at))}</time></td>
                  <td><span className="audit-actor">{actorName}</span>{actorEmail && <small className="audit-subtext">{actorEmail}</small>}</td>
                  <td><span className="audit-action">{describeAction(event.action)}</span><small className="audit-subtext">{describeDetails(event.details) || "—"}</small></td>
                  <td><span className="audit-resource">{event.resource_type.replaceAll("_", " ")}</span><small className="audit-subtext">{shortId(event.resource_id)}</small></td>
                  <td><span className={`outcome-pill outcome-${event.outcome}`}>{event.outcome}</span></td>
                </tr>;
              })}</tbody>
            </table>
          </div>
        )}
      </section>
    </PageFrame>
  );
}
