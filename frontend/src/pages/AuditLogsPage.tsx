import { useEffect, useState } from "react";
import { CheckCircle2, CircleX, FileClock, ShieldAlert } from "lucide-react";
import { PageFrame } from "../components/PageFrame";
import { formatDate, RefreshButton, TablePagination, TableToolbar, useTableView, useWorkspaceResource } from "../components/WorkspacePage";
import { RecordDrawer, ViewAction } from "../components/RecordDrawer";
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
  const [identityError, setIdentityError] = useState("");
  const query = new URLSearchParams();
  if (workspaceId) query.set("workspace_id", workspaceId);
  const resource = useWorkspaceResource<AuditEvent[]>(identityLoaded && (isPlatformAdmin || workspaceId) ? `/audit-logs?${query}` : null, 0, "array");
  const events = resource.data || [];
  const loading = !identityLoaded || resource.loading;
  const error = identityError || resource.error;
  const metricValue = (count: number) => resource.data ? count : "—";
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null);
  const table = useTableView(events, (event) => `${event.actor_type} ${event.actor_id || ""} ${event.details.actor_name || ""} ${event.details.actor_email || ""} ${event.action} ${event.resource_type} ${event.resource_id || ""} ${event.outcome} ${describeDetails(event.details)}`, { resetKey: workspaceId, filters: [{ id: "outcome", label: "Result", getValue: (event) => event.outcome }, { id: "resource", label: "Resource", getValue: (event) => event.resource_type }] });
  useEffect(() => setSelectedEvent(null), [workspaceId]);

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
      .then((session) => { if (!controller.signal.aborted) setIsPlatformAdmin(session.user.is_platform_admin); })
      .catch((caught) => {
        if (!controller.signal.aborted) setIdentityError(caught instanceof Error ? caught.message : "Could not verify your audit access.");
      })
      .finally(() => { if (!controller.signal.aborted) setIdentityLoaded(true); });
    return () => controller.abort();
  }, []);

  return (
    <PageFrame eyebrow="GOVERNANCE" title="Audit Logs" description="Review authenticated activity, access decisions and privileged workspace changes.">
      <section className="audit-summary-row">
        <button type="button" className="audit-summary-card metric-filter-card" disabled={!resource.data} onClick={table.resetFilters}><span className="audit-summary-icon"><FileClock size={16} /></span><span><strong>{metricValue(events.length)}</strong><small>Recorded events</small></span></button>
        <button type="button" className="audit-summary-card metric-filter-card green" disabled={!resource.data} onClick={() => table.setFilterValue("outcome", "succeeded")}><span className="audit-summary-icon"><CheckCircle2 size={16} /></span><span><strong>{metricValue(events.filter((event) => event.outcome === "succeeded").length)}</strong><small>Succeeded</small></span></button>
        <button type="button" className="audit-summary-card metric-filter-card amber" disabled={!resource.data} onClick={() => table.setFilterValue("outcome", "denied")}><span className="audit-summary-icon"><ShieldAlert size={16} /></span><span><strong>{metricValue(events.filter((event) => event.outcome === "denied").length)}</strong><small>Access denied</small></span></button>
        <button type="button" className="audit-summary-card metric-filter-card red" disabled={!resource.data} onClick={() => table.setFilterValue("outcome", "failed")}><span className="audit-summary-icon"><CircleX size={16} /></span><span><strong>{metricValue(events.filter((event) => event.outcome === "failed").length)}</strong><small>Failed</small></span></button>
      </section>
      <section className="settings-panel audit-panel">
        <div className="settings-panel-heading"><div className="settings-panel-icon"><FileClock size={17} /></div><div><h2>Activity history</h2><p>{workspaceId ? "Events scoped to the selected workspace." : "Platform-wide events visible to platform administrators."} Filter and page through the recorded history.</p></div><RefreshButton onClick={resource.refresh} busy={loading} /></div>
        {error && <div className="settings-notice is-error audit-error" role="alert">{error}</div>}
        {loading ? <div className="settings-empty">Loading audit history…</div> : !isPlatformAdmin && !workspaceId ? <div className="settings-empty">Select a workspace to view its audit history.</div> : error && !resource.data ? <div className="settings-empty">Audit history is unavailable. Retry when the connection is restored.</div> : events.length === 0 ? <div className="settings-empty">No audit events have been recorded for this scope yet.</div> : (
          <div className="member-table-wrap">
            <TableToolbar query={table.query} onQueryChange={table.setQuery} filteredCount={table.filteredItems.length} totalCount={table.totalItems} pageSize={table.pageSize} onPageSizeChange={table.setPageSize} label="audit events" filters={table.filters} onFilterChange={table.setFilterValue} onResetFilters={table.resetFilters} isFiltering={table.isFiltering} />
            {table.filteredItems.length === 0 ? <div className="settings-empty">No audit events match this filter.</div> : <>
            <table className="audit-table has-actions"><caption className="sr-only">Recent audit events and access decisions</caption><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Resource</th><th>Result</th><th>Actions</th></tr></thead>
              <tbody>{table.pageItems.map((event) => {
                const actorName = typeof event.details.actor_name === "string" ? event.details.actor_name : event.actor_id || "System";
                const actorEmail = typeof event.details.actor_email === "string" ? event.details.actor_email : "";
                return <tr key={event.id}>
                  <td><time dateTime={event.created_at}>{formatDate(event.created_at)}</time></td>
                  <td><span className="audit-actor">{actorName}</span>{actorEmail && <small className="audit-subtext">{actorEmail}</small>}</td>
                  <td><span className="audit-action">{describeAction(event.action)}</span><small className="audit-subtext">{describeDetails(event.details) || "—"}</small></td>
                  <td><span className="audit-resource">{event.resource_type.replaceAll("_", " ")}</span><small className="audit-subtext">{shortId(event.resource_id)}</small></td>
                  <td><span className={`outcome-pill outcome-${event.outcome}`}>{event.outcome}</span></td>
                  <td><ViewAction onClick={() => setSelectedEvent(event)} label={`${describeAction(event.action)} audit event`} /></td>
                </tr>;
              })}</tbody>
            </table>
            <TablePagination firstItem={table.firstItem} lastItem={table.lastItem} totalItems={table.filteredItems.length} page={table.page} pageCount={table.pageCount} onPageChange={table.setPage} label="audit events" />
            </>}
          </div>
        )}
      </section>
      {selectedEvent && <RecordDrawer onClose={() => setSelectedEvent(null)} record={{ title: describeAction(selectedEvent.action), eyebrow: "AUDIT EVENT", status: selectedEvent.outcome, description: `Recorded ${formatDate(selectedEvent.created_at)}`, sections: [{ title: "Activity", fields: [{ label: "Actor", value: selectedEvent.details.actor_name ? String(selectedEvent.details.actor_name) : selectedEvent.actor_id || "System" }, { label: "Actor email", value: selectedEvent.details.actor_email ? String(selectedEvent.details.actor_email) : null }, { label: "Actor type", value: selectedEvent.actor_type }, { label: "Occurred", value: formatDate(selectedEvent.created_at) }] }, { title: "Resource", fields: [{ label: "Type", value: selectedEvent.resource_type.replaceAll("_", " ") }, { label: "Record ID", value: selectedEvent.resource_id, mono: true }, { label: "Event ID", value: selectedEvent.id, mono: true }, { label: "Details", value: describeDetails(selectedEvent.details) }] }] }} />}
    </PageFrame>
  );
}
