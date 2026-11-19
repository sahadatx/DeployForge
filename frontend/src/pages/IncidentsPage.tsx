import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertTriangle, ArrowUpRight, BellRing, CircleAlert, Eye, RotateCcw, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { apiPost, type Deployment, type GitOpsRun, type GitOpsTarget, type Incident, type ServiceCatalog, type Slo, type SloAlert } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { RecordDrawer, type RecordDetails } from "../components/RecordDrawer";
import { formatDate, ResourceErrors, ResourceStatus, TablePagination, TableToolbar, useTableView, useWorkspaceResource, WorkspacePage } from "../components/WorkspacePage";
import IncidentLifecycle from "./IncidentLifecycle";

export default function IncidentsPage() {
  const { workspaceId, environmentId, canOperate, role } = usePlatformContext();
  const canRespond = canOperate || role === "service_owner";
  const alerts = useWorkspaceResource<SloAlert[]>(workspaceId ? `/workspaces/${workspaceId}/alerts` : null, 20_000, "array");
  const deployments = useWorkspaceResource<Deployment[]>(workspaceId ? `/workspaces/${workspaceId}/deployments?limit=100` : null, 20_000, "array");
  const gitopsRuns = useWorkspaceResource<GitOpsRun[]>(workspaceId ? `/workspaces/${workspaceId}/gitops-runs?limit=100` : null, 20_000, "array");
  const gitopsTargets = useWorkspaceResource<GitOpsTarget[]>(workspaceId ? `/workspaces/${workspaceId}/gitops-targets` : null, 60_000);
  const slos = useWorkspaceResource<Slo[]>(workspaceId ? `/workspaces/${workspaceId}/slos` : null, 60_000);
  const services = useWorkspaceResource<ServiceCatalog>(workspaceId ? `/workspaces/${workspaceId}/services` : null, 60_000, "catalog");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [selectedRecord, setSelectedRecord] = useState<RecordDetails | null>(null);
  const scopeKey = `${workspaceId}:${environmentId}`;
  const actionScope = useRef(scopeKey);
  actionScope.current = scopeKey;
  useEffect(() => { setSelectedRecord(null); setBusy(""); setError(""); setMessage(""); }, [workspaceId, environmentId]);
  const serviceMap = useMemo(() => new Map((services.data?.items || []).map((service) => [service.id, service])), [services.data]);
  const sloMap = useMemo(() => new Map((slos.data || []).map((slo) => [slo.id, slo])), [slos.data]);
  const gitopsEnvironmentMap = useMemo(() => new Map((gitopsTargets.data || []).map((target) => [target.id, target.environment_id])), [gitopsTargets.data]);
  const activeAlerts = alerts.data?.filter((alert) => {
    if (alert.status !== "firing" && alert.status !== "acknowledged") return false;
    const slo = sloMap.get(alert.slo_id);
    return !environmentId || Boolean(slo && (!slo.environment_id || slo.environment_id === environmentId));
  }) || [];
  const failedDeployments = deployments.data?.filter((deployment) => deployment.status === "failed" && (!environmentId || deployment.environment_id === environmentId)) || [];
  const troubledGitOpsRuns = gitopsRuns.data?.filter((run) => ["failed", "blocked"].includes(run.status) && (!environmentId || (run.target_id ? gitopsEnvironmentMap.get(run.target_id) === environmentId : false))) || [];
  const openCount = activeAlerts.length + failedDeployments.length + troubledGitOpsRuns.length;
  const signalItems = useMemo(() => [
    ...activeAlerts.map((alert) => ({ type: "alert" as const, id: alert.id, search: `${alert.summary} ${alert.status} ${sloMap.get(alert.slo_id)?.name || ""} ${serviceMap.get(sloMap.get(alert.slo_id)?.service_id || "")?.name || ""}`, alert })),
    ...failedDeployments.map((deployment) => ({ type: "deployment" as const, id: deployment.id, search: `${deployment.version} ${serviceMap.get(deployment.service_id)?.name || ""} ${deployment.failure_message || deployment.failure_code || ""}`, deployment })),
    ...troubledGitOpsRuns.map((run) => ({ type: "gitops" as const, id: run.id, search: `${run.application_name} ${run.status} ${run.failure_message || run.failure_code || ""}`, run })),
  ], [activeAlerts, failedDeployments, serviceMap, sloMap, troubledGitOpsRuns]);
  const incidentTable = useTableView(signalItems, (item) => item.search, {
    resetKey: `${workspaceId}:${environmentId}`,
    filters: [
      { id: "type", label: "Signal type", getValue: (item) => item.type, options: [{ value: "alert", label: "SLO alerts" }, { value: "deployment", label: "Failed releases" }, { value: "gitops", label: "GitOps failures" }] },
      { id: "status", label: "Status", getValue: (item) => item.type === "alert" ? item.alert.status : item.type === "deployment" ? item.deployment.status : item.run.status, options: [{ value: "firing", label: "Firing" }, { value: "acknowledged", label: "Acknowledged" }, { value: "failed", label: "Failed" }, { value: "blocked", label: "Blocked" }] },
    ],
  });
  const selectedType = incidentTable.filters.find((filter) => filter.id === "type")?.value || "";

  function filterSignals(type = "") {
    incidentTable.resetFilters();
    incidentTable.setFilterValue("type", type);
  }

  function viewSignal(item: (typeof signalItems)[number]) {
    if (item.type === "alert") {
      const alert = item.alert;
      const slo = sloMap.get(alert.slo_id);
      setSelectedRecord({ title: alert.summary, eyebrow: "RELIABILITY ALERT", status: alert.status, sections: [{ title: "Signal", fields: [{ label: "Service", value: slo ? serviceMap.get(slo.service_id)?.name || slo.service_id : null }, { label: "Service objective", value: slo?.name || alert.slo_id }, { label: "Opened", value: formatDate(alert.created_at) }, { label: "Acknowledged", value: formatDate(alert.acknowledged_at) }, { label: "Alert ID", value: alert.id, mono: true }] }] });
    } else if (item.type === "deployment") {
      const release = item.deployment;
      setSelectedRecord({ title: `Release ${release.version}`, eyebrow: "FAILED RELEASE", status: release.status, sections: [{ title: "Release", fields: [{ label: "Service", value: serviceMap.get(release.service_id)?.name || release.service_id }, { label: "Source", value: release.source_ref }, { label: "Failure code", value: release.failure_code }, { label: "Failure message", value: release.failure_message }, { label: "Attempts", value: `${release.attempt_count} / ${release.max_attempts}` }, { label: "Updated", value: formatDate(release.updated_at) }, { label: "Deployment ID", value: release.id, mono: true }] }] });
    } else {
      const run = item.run;
      setSelectedRecord({ title: run.application_name, eyebrow: "GITOPS SIGNAL", status: run.status, sections: [{ title: "Rollout", fields: [{ label: "Action", value: run.action_kind.replaceAll("_", " ") }, { label: "Failure code", value: run.failure_code }, { label: "Failure message", value: run.failure_message }, { label: "Created", value: formatDate(run.created_at) }, { label: "Updated", value: formatDate(run.updated_at) }, { label: "Run ID", value: run.id, mono: true }] }] });
    }
  }

  async function acknowledge(alert: SloAlert) {
    if (busy) return;
    const scope = scopeKey;
    setBusy(alert.id); setError(""); setMessage("");
    try {
      await apiPost(`/workspaces/${workspaceId}/alerts/${alert.id}/acknowledge`);
      if (actionScope.current !== scope) return;
      setMessage("SLO alert acknowledged and recorded in audit history."); alerts.refresh();
    } catch (caught) { if (actionScope.current === scope) setError(caught instanceof Error ? caught.message : "Alert could not be acknowledged."); }
    finally { if (actionScope.current === scope) setBusy(""); }
  }

  async function retryRun(run: GitOpsRun) {
    if (busy) return;
    const scope = scopeKey;
    setBusy(run.id); setError(""); setMessage("");
    try {
      await apiPost(`/workspaces/${workspaceId}/gitops-runs/${run.id}/retry`);
      if (actionScope.current !== scope) return;
      setMessage("Blocked GitOps run queued for retry."); gitopsRuns.refresh();
    } catch (caught) { if (actionScope.current === scope) setError(caught instanceof Error ? caught.message : "GitOps run could not be retried."); }
    finally { if (actionScope.current === scope) setBusy(""); }
  }

  async function createSignalIncident(type: "slo_alert" | "deployment" | "gitops", id: string, title: string) {
    if (busy || !canRespond || !workspaceId) return;
    const scope = scopeKey;
    setBusy(id); setError(""); setMessage("");
    try {
      const incident = await apiPost<Incident>(`/workspaces/${workspaceId}/incidents`, {
        title: title.slice(0, 240), severity: type === "slo_alert" ? "sev1" : "sev2", source_type: type, source_id: id,
      });
      if (actionScope.current !== scope) return;
      window.dispatchEvent(new CustomEvent("deployforge:incident-created", { detail: incident.id }));
      setMessage("Signal linked to the incident lifecycle record.");
    } catch (caught) { if (actionScope.current === scope) setError(caught instanceof Error ? caught.message : "Signal could not be linked to an incident."); }
    finally { if (actionScope.current === scope) setBusy(""); }
  }

  const loading = alerts.loading || deployments.loading || gitopsRuns.loading || (Boolean(environmentId) && (slos.loading || gitopsTargets.loading));
  const countsAvailable = Boolean(alerts.data && deployments.data && gitopsRuns.data && (!environmentId || (slos.data && gitopsTargets.data)));
  const queueUnavailable = !loading && !countsAvailable;
  const metricValue = (count: number) => countsAvailable ? count : "—";

  return <WorkspacePage eyebrow="RELIABILITY" title="Incidents" description="Triage live SLO alerts and release failures, then follow recovery from their source records.">
    <ResourceErrors resources={[{ label: "SLO alerts", error: alerts.error }, { label: "Deployments", error: deployments.error }, { label: "GitOps runs", error: gitopsRuns.error }, { label: "GitOps targets", error: gitopsTargets.error }, { label: "Service objectives", error: slos.error }, { label: "Services", error: services.error }]} />
    <div className="metric-grid metric-grid-4"><Metric label="Open signals" value={metricValue(openCount)} icon={<BellRing size={16} />} tone={!countsAvailable ? "neutral" : openCount ? "red" : "green"} active={!incidentTable.hasActiveFilters} onClick={() => filterSignals()} /><Metric label="SLO alerts" value={metricValue(activeAlerts.length)} icon={<Activity size={16} />} tone="amber" active={selectedType === "alert"} onClick={() => filterSignals("alert")} /><Metric label="Failed releases" value={metricValue(failedDeployments.length)} icon={<CircleAlert size={16} />} tone="red" active={selectedType === "deployment"} onClick={() => filterSignals("deployment")} /><Metric label="GitOps failures" value={metricValue(troubledGitOpsRuns.length)} icon={<AlertTriangle size={16} />} tone="violet" active={selectedType === "gitops"} onClick={() => filterSignals("gitops")} /></div>
    <IncidentLifecycle services={services.data} />
    {(message || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || message}</div>}
    <section className="incident-context"><span className="incident-context-icon"><ShieldCheck size={16} /></span><span><strong>Operational signals from live platform data</strong><small>This view combines SLO alert records with failed deployment and GitOps runs. Alert acknowledgement and rollout retry update their source systems and audit trails.</small></span><Link to="/observability">Open observability <span>→</span></Link></section>
    <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Active response queue</h2><p>Unresolved SLO alerts and release failures in the selected environment.</p></div><button className="catalog-secondary-button" onClick={() => { alerts.refresh(); deployments.refresh(); gitopsRuns.refresh(); gitopsTargets.refresh(); slos.refresh(); }}>Refresh queue</button></div>
      {loading ? <div className="resource-loading"><span className="spinner" />Loading live response signals…</div> : queueUnavailable && openCount === 0 ? <div className="resource-empty"><div className="empty-state-icon"><AlertTriangle size={17} /></div><strong>Response signals are unavailable</strong><p>Refresh the queue when its source data is available.</p></div> : openCount === 0 ? <div className="resource-empty"><div className="empty-state-icon"><ShieldCheck size={17} /></div><strong>No active reliability signals</strong><p>Active SLO alerts and release failures will appear here as they are recorded.</p></div> : <>
        {queueUnavailable && <p className="settings-notice is-error" role="status">Some signal sources are unavailable. The queue shows the records currently available.</p>}
        <TableToolbar query={incidentTable.query} onQueryChange={incidentTable.setQuery} filteredCount={incidentTable.filteredItems.length} totalCount={incidentTable.totalItems} pageSize={incidentTable.pageSize} onPageSizeChange={incidentTable.setPageSize} filters={incidentTable.filters} onFilterChange={incidentTable.setFilterValue} onResetFilters={incidentTable.resetFilters} isFiltering={incidentTable.isFiltering} label="response signals" />
        {incidentTable.pageItems.length === 0 ? <div className="resource-empty compact">No signals match this filter.</div> : <div className="incident-list">{incidentTable.pageItems.map((item) => {
          if (item.type === "alert") { const alert = item.alert; const slo = sloMap.get(alert.slo_id); const service = slo ? serviceMap.get(slo.service_id) : null; return <article className="incident-row" key={`alert-${alert.id}`}><span className="incident-severity incident-high"><BellRing size={16} /></span><div className="incident-main"><div className="incident-title-row"><strong>{alert.summary}</strong><ResourceStatus value={alert.status} /></div><p>{service?.name || "Service"}{slo ? ` · ${slo.name}` : ""} · SLO evaluation indicates objective breach.</p><small>Opened {formatDate(alert.created_at)}{alert.acknowledged_at ? ` · Acknowledged ${formatDate(alert.acknowledged_at)}` : ""}</small></div><div className="incident-actions">{canRespond && <button className="catalog-secondary-button" disabled={Boolean(busy)} onClick={() => void createSignalIncident("slo_alert", alert.id, alert.summary)}><ShieldCheck size={13} /> Create incident</button>}{alert.status === "firing" && canOperate && <button className="catalog-secondary-button" disabled={Boolean(busy)} onClick={() => void acknowledge(alert)}><ShieldCheck size={13} /> Acknowledge</button>}<button type="button" className="catalog-secondary-button" onClick={() => viewSignal(item)} aria-label={`View alert ${alert.summary}`}><Eye size={14} /> View alert</button></div></article>; }
          if (item.type === "deployment") { const deployment = item.deployment; return <article className="incident-row" key={`deployment-${deployment.id}`}><span className="incident-severity incident-medium"><CircleAlert size={16} /></span><div className="incident-main"><div className="incident-title-row"><strong>Release {deployment.version} failed</strong><ResourceStatus value={deployment.status} /></div><p>{serviceMap.get(deployment.service_id)?.name || "Service"} · {deployment.failure_message || deployment.failure_code || "Release run did not complete successfully."}</p><small>Updated {formatDate(deployment.updated_at)} · Attempt {deployment.attempt_count}/{deployment.max_attempts}</small></div><div className="incident-actions">{canRespond && <button type="button" className="catalog-secondary-button" disabled={Boolean(busy)} onClick={() => void createSignalIncident("deployment", deployment.id, `Release ${deployment.version} failed`)}><ShieldCheck size={13} /> Create incident</button>}<button type="button" className="catalog-secondary-button" onClick={() => viewSignal(item)} aria-label={`View failed release ${deployment.version}`}><Eye size={14} /> View release</button></div></article>; }
          const run = item.run; return <article className="incident-row" key={`gitops-${run.id}`}><span className="incident-severity incident-medium"><AlertTriangle size={16} /></span><div className="incident-main"><div className="incident-title-row"><strong>{run.application_name} {run.status === "blocked" ? "rollout blocked" : "rollout failed"}</strong><ResourceStatus value={run.status} /></div><p>{run.failure_message || run.failure_code || "GitOps reconciliation did not reach a healthy state."}</p><small>{run.action_kind.replaceAll("_", " ")} · Updated {formatDate(run.updated_at)}</small></div><div className="incident-actions">{canRespond && <button className="catalog-secondary-button" disabled={Boolean(busy)} onClick={() => void createSignalIncident("gitops", run.id, `${run.application_name} rollout ${run.status}`)}><ShieldCheck size={13} /> Create incident</button>}{run.status === "blocked" && canOperate && <button className="catalog-secondary-button" disabled={Boolean(busy)} onClick={() => void retryRun(run)}><RotateCcw size={13} /> Retry</button>}<button type="button" className="catalog-secondary-button" onClick={() => viewSignal(item)} aria-label={`View ${run.application_name} rollout`}><Eye size={14} /> View rollout</button></div></article>;
        })}</div>}
        <TablePagination firstItem={incidentTable.firstItem} lastItem={incidentTable.lastItem} totalItems={incidentTable.filteredItems.length} page={incidentTable.page} pageCount={incidentTable.pageCount} onPageChange={incidentTable.setPage} label="response signals" />
      </>}
    </section>
    <section className="incident-footer-note"><Activity size={14} /><span>Response history is append-only and mirrored into audit events. PagerDuty owns configured on-call routing; DeployForge records assignments, escalation levels and resolution evidence.</span></section>
    {selectedRecord && <RecordDrawer record={selectedRecord} onClose={() => setSelectedRecord(null)} />}
  </WorkspacePage>;
}

function Metric({ label, value, icon, tone, active, onClick }: { label: string; value: number | string; icon: React.ReactNode; tone: string; active: boolean; onClick: () => void }) { return <button type="button" className={`metric-card metric-action ${tone} ${active ? "is-active" : ""}`} aria-pressed={active} disabled={value === "—"} onClick={onClick} aria-label={`Show ${label.toLowerCase()}: ${value}`}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span><ArrowUpRight size={14} className="kpi-action-icon" /></button>; }
