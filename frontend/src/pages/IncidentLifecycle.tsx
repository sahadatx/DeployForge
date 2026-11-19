import { useEffect, useMemo, useState } from "react";
import { ArrowUpRight, BellRing, ClipboardCheck, Plus, RadioTower, ShieldAlert, UserRoundCheck } from "lucide-react";
import { apiGet, apiPost, apiPut, type Incident, type IncidentEvent, type IncidentPostmortem, type ServiceCatalog, type WorkspaceMember } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { FieldLabel, FormDrawer } from "../components/FormDrawer";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, TablePagination, TableToolbar, useTableView, useWorkspaceResource } from "../components/WorkspacePage";

export default function IncidentLifecycle({ services }: { services: ServiceCatalog | null }) {
  const { workspaceId, canOperate, role, session } = usePlatformContext();
  const canRespond = canOperate || role === "service_owner";
  const incidents = useWorkspaceResource<Incident[]>(workspaceId ? `/workspaces/${workspaceId}/incidents?limit=100` : null, 15_000, "array");
  const members = useWorkspaceResource<WorkspaceMember[]>(workspaceId ? `/workspaces/${workspaceId}/members` : null, 60_000);
  const [createOpen, setCreateOpen] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newSeverity, setNewSeverity] = useState<Incident["severity"]>("sev3");
  const [newServiceId, setNewServiceId] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [pmOpen, setPmOpen] = useState(false);
  const [postmortem, setPostmortem] = useState<IncidentPostmortem | null>(null);
  const [postmortemLoading, setPostmortemLoading] = useState(false);
  const [pmForm, setPmForm] = useState({ title: "", summary: "", impact: "", root_cause: "", detection: "", response: "", corrective_actions: "" });
  const [assigneeId, setAssigneeId] = useState("");
  const [escalationComment, setEscalationComment] = useState("Escalated for additional response coverage.");
  const [transitionStatus, setTransitionStatus] = useState<"investigating" | "mitigated" | "resolved" | "closed">("investigating");
  const [transitionComment, setTransitionComment] = useState("");
  const [resolution, setResolution] = useState("");
  const [reviewComment, setReviewComment] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const selected = incidents.data?.find((incident) => incident.id === selectedId) || null;
  const events = useWorkspaceResource<IncidentEvent[]>(workspaceId && selectedId ? `/workspaces/${workspaceId}/incidents/${selectedId}/events` : null, 10_000, "array");
  const incidentTable = useTableView(incidents.data || [], (incident) => `${incident.title} ${incident.description} ${incident.severity} ${incident.status}`, {
    resetKey: workspaceId,
    filters: [
      { id: "status", label: "Lifecycle", getValue: (incident) => incident.status, options: ["open", "investigating", "mitigated", "resolved", "closed"].map((value) => ({ value, label: value.replaceAll("_", " ") })) },
      { id: "severity", label: "Severity", getValue: (incident) => incident.severity, options: ["sev0", "sev1", "sev2", "sev3"].map((value) => ({ value, label: value.toUpperCase() })) },
    ],
  });
  const memberNames = useMemo(() => new Map((members.data || []).map((member) => [member.user.id, member.user.display_name || member.user.email])), [members.data]);
  const resolvedCount = incidents.data?.filter((incident) => ["resolved", "closed"].includes(incident.status)).length || 0;
  const activeCount = (incidents.data?.length || 0) - resolvedCount;

  useEffect(() => {
    const onCreated = (event: Event) => {
      const incidentId = (event as CustomEvent<string>).detail;
      if (!incidentId) return;
      incidents.refresh(); setSelectedId(incidentId); setError("");
    };
    window.addEventListener("deployforge:incident-created", onCreated);
    return () => window.removeEventListener("deployforge:incident-created", onCreated);
  }, [incidents.refresh]);

  function resetCreate() {
    setNewTitle(""); setNewDescription(""); setNewSeverity("sev3"); setNewServiceId("");
  }

  async function submitCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || !canRespond || busy) return;
    setBusy("create"); setError(""); setMessage("");
    try {
      const incident = await apiPost<Incident>(`/workspaces/${workspaceId}/incidents`, {
        title: newTitle.trim(), description: newDescription.trim(), severity: newSeverity,
        service_id: newServiceId || null,
      });
      incidents.refresh(); setCreateOpen(false); resetCreate(); setSelectedId(incident.id);
      setMessage("Incident created. Its response timeline and audit history are active.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Incident could not be created."); }
    finally { setBusy(""); }
  }

  async function act(action: string, body: unknown, label: string) {
    if (!workspaceId || !selected || !canRespond || busy) return;
    setBusy(action); setError(""); setMessage("");
    try {
      await apiPost(`/workspaces/${workspaceId}/incidents/${selected.id}/${action}`, body);
      incidents.refresh(); events.refresh(); setMessage(label);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Incident action could not be saved."); }
    finally { setBusy(""); }
  }

  async function openPostmortem() {
    if (!workspaceId || !selected) return;
    setPostmortemLoading(true); setError("");
    try {
      const value = await apiGet<IncidentPostmortem | null>(`/workspaces/${workspaceId}/incidents/${selected.id}/postmortem`);
      setPostmortem(value);
      setPmForm(value ? {
        title: value.title, summary: value.summary, impact: value.impact, root_cause: value.root_cause,
        detection: value.detection, response: value.response, corrective_actions: value.corrective_actions.join("\n"),
      } : { title: `Postmortem: ${selected.title}`, summary: "", impact: "", root_cause: "", detection: "", response: "", corrective_actions: "" });
      setPmOpen(true);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Postmortem could not be loaded."); }
    finally { setPostmortemLoading(false); }
  }

  async function savePostmortem(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || !selected || busy || !canRespond) return;
    setBusy("postmortem"); setError("");
    try {
      const value = await apiPut<IncidentPostmortem>(`/workspaces/${workspaceId}/incidents/${selected.id}/postmortem`, {
        ...pmForm, corrective_actions: pmForm.corrective_actions.split("\n").map((action) => action.trim()).filter(Boolean), due_at: null,
      });
      setPostmortem(value); events.refresh(); setMessage("Postmortem draft saved with an audit event.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Postmortem draft could not be saved."); }
    finally { setBusy(""); }
  }

  async function postmortemAction(action: "submit" | "review", body?: unknown) {
    if (!workspaceId || !selected || busy) return;
    setBusy(`postmortem-${action}`); setError("");
    try {
      const value = await apiPost<IncidentPostmortem>(`/workspaces/${workspaceId}/incidents/${selected.id}/postmortem/${action}`, body);
      setPostmortem(value); events.refresh();
      if (action === "submit") setMessage("Postmortem submitted for independent review.");
      else setMessage(body && (body as { decision?: string }).decision === "approve" ? "Postmortem independently reviewed and published." : "Postmortem returned to its author for revision.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Postmortem workflow could not be updated."); }
    finally { setBusy(""); }
  }

  const editablePostmortem = canRespond && (!postmortem || (postmortem.status === "draft" && postmortem.author_id === session.user.id));
  const loading = incidents.loading || members.loading;

  return <>
    <ResourceErrors resources={[{ label: "Incidents", error: incidents.error }, { label: "Workspace responders", error: members.error }]} />
    {(message || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || message}</div>}
    <div className="metric-grid metric-grid-3">
      <Metric label="Active incidents" value={incidents.data ? activeCount : "—"} icon={<BellRing size={16} />} />
      <Metric label="Resolved" value={incidents.data ? resolvedCount : "—"} icon={<ClipboardCheck size={16} />} />
      <Metric label="Escalated" value={incidents.data?.filter((item) => item.escalation_level > 0).length ?? "—"} icon={<ShieldAlert size={16} />} />
    </div>
    <section className="resource-panel incident-lifecycle-panel">
      <div className="resource-panel-heading"><div><h2>Incident records</h2><p>Audited ownership, escalation, resolution and reviewed postmortems.</p></div><div className="incident-actions"><button type="button" className="catalog-secondary-button" onClick={() => incidents.refresh()}><RadioTower size={13} /> Refresh</button>{canRespond && <button type="button" className="settings-primary-button" onClick={() => { resetCreate(); setCreateOpen(true); }}><Plus size={14} /> New incident</button>}</div></div>
      <TableToolbar query={incidentTable.query} onQueryChange={incidentTable.setQuery} filteredCount={incidentTable.filteredItems.length} totalCount={incidentTable.totalItems} pageSize={incidentTable.pageSize} onPageSizeChange={incidentTable.setPageSize} filters={incidentTable.filters} onFilterChange={incidentTable.setFilterValue} onResetFilters={incidentTable.resetFilters} isFiltering={incidentTable.isFiltering} label="incidents" />
      <LoadingOrEmpty loading={loading && !incidents.data} empty={!loading && incidentTable.filteredItems.length === 0} label="No incident records yet" />
      {!loading && incidentTable.pageItems.length > 0 && <div className="resource-table-wrap"><table className="resource-table"><thead><tr><th>Incident</th><th>Severity</th><th>Status</th><th>Owner</th><th>Escalation</th><th>Updated</th><th>Action</th></tr></thead><tbody>{incidentTable.pageItems.map((incident) => <tr key={incident.id}>
        <td><button type="button" className="incident-table-title" onClick={() => { setSelectedId(incident.id); setAssigneeId(incident.assigned_to || ""); setError(""); }}>{incident.title}</button><small className="incident-table-subtitle">{incident.source_type.replaceAll("_", " ")} · {incident.description || "No description"}</small></td>
        <td><span className={`incident-severity-tag ${incident.severity}`}>{incident.severity.toUpperCase()}</span></td><td><ResourceStatus value={incident.status} /></td><td>{incident.assigned_to ? memberNames.get(incident.assigned_to) || "Workspace member" : "Unassigned"}</td><td>Level {incident.escalation_level}</td><td>{formatDate(incident.updated_at)}</td><td><button type="button" className="catalog-secondary-button" onClick={() => { setSelectedId(incident.id); setAssigneeId(incident.assigned_to || ""); setError(""); }}><ArrowUpRight size={13} /> Open</button></td>
      </tr>)}</tbody></table></div>}
      <TablePagination firstItem={incidentTable.firstItem} lastItem={incidentTable.lastItem} totalItems={incidentTable.filteredItems.length} page={incidentTable.page} pageCount={incidentTable.pageCount} onPageChange={incidentTable.setPage} label="incidents" />
    </section>

    {createOpen && <FormDrawer title="Open an incident" eyebrow="INCIDENT INTAKE" description="Create a workspace-scoped response record with an immutable event history." dirty={Boolean(newTitle || newDescription || newServiceId || newSeverity !== "sev3")} onClose={() => setCreateOpen(false)} onReset={resetCreate} busy={busy === "create"} error={error} actions={<button form="incident-create-form" type="submit" className="settings-primary-button" disabled={Boolean(busy)}><Plus size={14} />Open incident</button>}>
      {!canRespond ? <p className="permission-note">Workspace responders can open an incident.</p> : <form id="incident-create-form" className="resource-form" onSubmit={(event) => void submitCreate(event)}>
        <label className="form-wide"><FieldLabel required>Title</FieldLabel><input required minLength={3} maxLength={240} value={newTitle} onChange={(event) => setNewTitle(event.target.value)} placeholder="Checkout latency elevated" /></label>
        <label><FieldLabel required>Severity</FieldLabel><select value={newSeverity} onChange={(event) => setNewSeverity(event.target.value as Incident["severity"])}><option value="sev0">SEV0 · Critical</option><option value="sev1">SEV1 · High</option><option value="sev2">SEV2 · Medium</option><option value="sev3">SEV3 · Low</option></select></label>
        <label><FieldLabel optional>Service</FieldLabel><select value={newServiceId} onChange={(event) => setNewServiceId(event.target.value)}><option value="">Workspace wide</option>{services?.items.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select></label>
        <label className="form-wide"><FieldLabel optional>Description</FieldLabel><textarea rows={4} maxLength={8000} value={newDescription} onChange={(event) => setNewDescription(event.target.value)} placeholder="Customer impact, scope, or initial symptoms" /></label>
      </form>}
    </FormDrawer>}

    {selected && <FormDrawer title={selected.title} eyebrow={`${selected.severity.toUpperCase()} INCIDENT · ${selected.source_type.replaceAll("_", " ").toUpperCase()}`} description={`Opened ${formatDate(selected.opened_at)} · ${selected.id}`} onClose={() => { setSelectedId(""); setError(""); }} busy={Boolean(busy)} error={error} actions={canRespond && ["resolved", "closed"].includes(selected.status) ? <button type="button" className="catalog-secondary-button" onClick={() => void openPostmortem()} disabled={postmortemLoading}><ClipboardCheck size={14} />{postmortemLoading ? "Loading…" : "Postmortem"}</button> : undefined}>
      <div className="incident-detail-stack">
        <section className="incident-detail-card"><div className="incident-detail-heading"><strong>Current response</strong><ResourceStatus value={selected.status} /></div><p>{selected.description || "No description was supplied."}</p><dl><div><dt>Service</dt><dd>{services?.items.find((service) => service.id === selected.service_id)?.name || (selected.service_id ? "Service" : "Workspace wide")}</dd></div><div><dt>Responder</dt><dd>{selected.assigned_to ? memberNames.get(selected.assigned_to) || "Workspace member" : "Unassigned"}</dd></div><div><dt>PagerDuty</dt><dd>{selected.pagerduty_triggered_at ? `Triggered ${formatDate(selected.pagerduty_triggered_at)}` : "Delivery pending or not configured"}</dd></div><div><dt>Escalation</dt><dd>Level {selected.escalation_level}{selected.next_escalation_at ? ` · Next ${formatDate(selected.next_escalation_at)}` : ""}</dd></div></dl>{selected.resolution && <p><strong>Resolution:</strong> {selected.resolution}</p>}</section>
        {canRespond && !["resolved", "closed"].includes(selected.status) && <>
          <section className="incident-detail-card"><h3><UserRoundCheck size={14} /> Responder assignment</h3><div className="incident-action-form"><select aria-label="Incident responder" value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)}><option value="">Unassigned</option>{members.data?.filter((member) => member.user.active).map((member) => <option key={member.user.id} value={member.user.id}>{member.user.display_name || member.user.email} · {member.role.replaceAll("_", " ")}</option>)}</select><button type="button" className="catalog-secondary-button" disabled={Boolean(busy)} onClick={() => void act("assign", { assignee_id: assigneeId || null }, "Responder assignment recorded.")}>Save assignment</button></div></section>
          <section className="incident-detail-card"><h3><RadioTower size={14} /> Escalation</h3><div className="incident-action-form"><input aria-label="Escalation note" value={escalationComment} onChange={(event) => setEscalationComment(event.target.value)} placeholder="Escalation note" /><button type="button" className="catalog-secondary-button" disabled={Boolean(busy) || escalationComment.trim().length < 3} onClick={() => void act("escalate", { comment: escalationComment.trim(), assignee_id: assigneeId || null }, "Escalation level and response note recorded.")}>Escalate to level {selected.escalation_level + 1}</button></div><small className="incident-inline-note">PagerDuty's configured service owns external on-call schedules and paging escalation.</small></section>
          <section className="incident-detail-card"><h3>Lifecycle update</h3><div className="incident-action-form"><select aria-label="New incident status" value={transitionStatus} onChange={(event) => setTransitionStatus(event.target.value as typeof transitionStatus)}>{availableTransitions(selected.status).map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select><input aria-label="Status update note" value={transitionComment} onChange={(event) => setTransitionComment(event.target.value)} placeholder="What changed?" /></div>{transitionStatus === "resolved" && <label className="incident-resolution-field"><FieldLabel required>Resolution summary</FieldLabel><textarea rows={3} value={resolution} onChange={(event) => setResolution(event.target.value)} placeholder="Describe how service was restored" /></label>}<button type="button" className="settings-primary-button incident-transition-button" disabled={Boolean(busy) || transitionComment.trim().length < 3 || (transitionStatus === "resolved" && resolution.trim().length < 3)} onClick={() => void act("transition", { status: transitionStatus, comment: transitionComment.trim(), resolution: transitionStatus === "resolved" ? resolution.trim() : null }, `Incident moved to ${transitionStatus.replaceAll("_", " ")}.`)}>Save lifecycle update</button></section>
        </>}
        <section className="incident-detail-card"><h3>Response timeline</h3>{events.loading ? <div className="resource-loading"><span className="spinner" />Loading timeline…</div> : events.error ? <div className="settings-notice is-error" role="alert">{events.error}</div> : <div className="incident-timeline">{(events.data || []).map((event) => <article key={event.id}><span className="incident-timeline-marker" /><div><strong>{event.message}</strong><small>{event.event_type.replaceAll("_", " ")} · {memberNames.get(event.actor_id || "") || (event.actor_type === "system" ? "System" : "Responder")} · {formatDate(event.created_at)}</small>{event.from_status && <small>{event.from_status} → {event.to_status}</small>}</div></article>)}</div>}</section>
      </div>
    </FormDrawer>}

    {pmOpen && selected && <FormDrawer title="Incident postmortem" eyebrow={`AFTER ACTION · ${selected.severity.toUpperCase()}`} description="Capture impact and corrective work, then submit for review by someone other than the author." onClose={() => setPmOpen(false)} busy={Boolean(busy)} error={error} readOnly={!editablePostmortem} actions={postmortem?.status === "in_review" && postmortem.author_id !== session.user.id && canRespond ? <><input className="incident-review-comment" aria-label="Review comment" value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} placeholder="Review decision and rationale" /><button type="button" className="catalog-secondary-button" disabled={Boolean(busy) || reviewComment.trim().length < 3} onClick={() => void postmortemAction("review", { decision: "reject", comment: reviewComment.trim() })}>Request changes</button><button type="button" className="settings-primary-button" disabled={Boolean(busy) || reviewComment.trim().length < 3} onClick={() => void postmortemAction("review", { decision: "approve", comment: reviewComment.trim() })}><ClipboardCheck size={14} />Approve and publish</button></> : editablePostmortem && canRespond ? <><button type="button" className="catalog-secondary-button" disabled={Boolean(busy) || !postmortem || postmortem.status !== "draft" || postmortem.author_id !== session.user.id} onClick={() => void postmortemAction("submit")}>Submit for review</button><button form="postmortem-form" type="submit" className="settings-primary-button" disabled={Boolean(busy)}><ClipboardCheck size={14} />Save draft</button></> : undefined}>
      {postmortem && <div className="incident-postmortem-state"><ResourceStatus value={postmortem.status} />{postmortem.review_comment && <span>{postmortem.review_comment}</span>}{postmortem.published_at && <small>Published {formatDate(postmortem.published_at)}</small>}</div>}
      {!postmortemLoading && <form id="postmortem-form" className="resource-form incident-postmortem-form" onSubmit={(event) => void savePostmortem(event)}>
        <label className="form-wide"><FieldLabel required>Report title</FieldLabel><input required minLength={3} maxLength={240} disabled={!editablePostmortem} value={pmForm.title} onChange={(event) => setPmForm({ ...pmForm, title: event.target.value })} /></label>
        <label className="form-wide"><FieldLabel required>Executive summary</FieldLabel><textarea required minLength={10} maxLength={8000} rows={3} disabled={!editablePostmortem} value={pmForm.summary} onChange={(event) => setPmForm({ ...pmForm, summary: event.target.value })} /></label>
        <label className="form-wide"><FieldLabel required>Customer impact</FieldLabel><textarea required minLength={10} maxLength={8000} rows={3} disabled={!editablePostmortem} value={pmForm.impact} onChange={(event) => setPmForm({ ...pmForm, impact: event.target.value })} /></label>
        <label className="form-wide"><FieldLabel required>Root cause</FieldLabel><textarea required minLength={10} maxLength={8000} rows={3} disabled={!editablePostmortem} value={pmForm.root_cause} onChange={(event) => setPmForm({ ...pmForm, root_cause: event.target.value })} /></label>
        <label><FieldLabel required>Detection</FieldLabel><textarea required minLength={10} maxLength={8000} rows={3} disabled={!editablePostmortem} value={pmForm.detection} onChange={(event) => setPmForm({ ...pmForm, detection: event.target.value })} /></label>
        <label><FieldLabel required>Response</FieldLabel><textarea required minLength={10} maxLength={8000} rows={3} disabled={!editablePostmortem} value={pmForm.response} onChange={(event) => setPmForm({ ...pmForm, response: event.target.value })} /></label>
        <label className="form-wide"><FieldLabel required>Corrective actions</FieldLabel><textarea required minLength={3} maxLength={5000} rows={5} disabled={!editablePostmortem} value={pmForm.corrective_actions} onChange={(event) => setPmForm({ ...pmForm, corrective_actions: event.target.value })} placeholder="One action per line" /><small className="incident-inline-note">Use one action per line; record an owner and due date in each action.</small></label>
      </form>}
    </FormDrawer>}
  </>;
}

function availableTransitions(status: Incident["status"]): Array<"investigating" | "mitigated" | "resolved" | "closed"> {
  if (status === "open") return ["investigating", "mitigated", "resolved"];
  if (status === "investigating") return ["mitigated", "resolved"];
  if (status === "mitigated") return ["investigating", "resolved"];
  if (status === "resolved") return ["investigating", "closed"];
  return [];
}

function Metric({ label, value, icon }: { label: string; value: number | string; icon: React.ReactNode }) {
  return <div className="metric-card"><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span></div>;
}
