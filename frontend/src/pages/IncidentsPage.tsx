import { useMemo, useState } from "react";
import { Activity, AlertTriangle, BellRing, CircleAlert, RotateCcw, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { apiPost, type Deployment, type GitOpsRun, type GitOpsTarget, type ServiceCatalog, type Slo, type SloAlert } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { formatDate, ResourceErrors, ResourceStatus, useWorkspaceResource, WorkspacePage } from "../components/WorkspacePage";

export default function IncidentsPage() {
  const { workspaceId, environmentId, canOperate } = usePlatformContext();
  const alerts = useWorkspaceResource<SloAlert[]>(workspaceId ? `/workspaces/${workspaceId}/alerts?limit=200` : null);
  const deployments = useWorkspaceResource<Deployment[]>(workspaceId ? `/workspaces/${workspaceId}/deployments?limit=100` : null);
  const gitopsRuns = useWorkspaceResource<GitOpsRun[]>(workspaceId ? `/workspaces/${workspaceId}/gitops-runs?limit=100` : null);
  const gitopsTargets = useWorkspaceResource<GitOpsTarget[]>(workspaceId ? `/workspaces/${workspaceId}/gitops-targets` : null, 60_000);
  const slos = useWorkspaceResource<Slo[]>(workspaceId ? `/workspaces/${workspaceId}/slos` : null, 60_000);
  const services = useWorkspaceResource<ServiceCatalog>(workspaceId ? `/workspaces/${workspaceId}/services?limit=100` : null, 60_000);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const serviceMap = useMemo(() => new Map((services.data?.items || []).map((service) => [service.id, service])), [services.data]);
  const sloMap = useMemo(() => new Map((slos.data || []).map((slo) => [slo.id, slo])), [slos.data]);
  const gitopsEnvironmentMap = useMemo(() => new Map((gitopsTargets.data || []).map((target) => [target.id, target.environment_id])), [gitopsTargets.data]);
  const activeAlerts = alerts.data?.filter((alert) => {
    if (alert.status !== "firing" && alert.status !== "acknowledged") return false;
    const slo = sloMap.get(alert.slo_id);
    return !environmentId || !slo?.environment_id || slo.environment_id === environmentId;
  }) || [];
  const failedDeployments = deployments.data?.filter((deployment) => deployment.status === "failed" && (!environmentId || deployment.environment_id === environmentId)) || [];
  const troubledGitOpsRuns = gitopsRuns.data?.filter((run) => ["failed", "blocked"].includes(run.status) && (!environmentId || (run.target_id ? gitopsEnvironmentMap.get(run.target_id) === environmentId : false))) || [];
  const openCount = activeAlerts.length + failedDeployments.length + troubledGitOpsRuns.length;

  async function acknowledge(alert: SloAlert) {
    setBusy(alert.id); setError(""); setMessage("");
    try {
      await apiPost(`/workspaces/${workspaceId}/alerts/${alert.id}/acknowledge`);
      setMessage("SLO alert acknowledged and recorded in audit history."); alerts.refresh();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Alert could not be acknowledged."); }
    finally { setBusy(""); }
  }

  async function retryRun(run: GitOpsRun) {
    setBusy(run.id); setError(""); setMessage("");
    try {
      await apiPost(`/workspaces/${workspaceId}/gitops-runs/${run.id}/retry`);
      setMessage("Blocked GitOps run queued for retry."); gitopsRuns.refresh();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "GitOps run could not be retried."); }
    finally { setBusy(""); }
  }

  const loading = alerts.loading || deployments.loading || gitopsRuns.loading || (Boolean(environmentId) && (slos.loading || gitopsTargets.loading));

  return <WorkspacePage eyebrow="RELIABILITY" title="Incidents" description="Triage live SLO alerts and release failures, then follow recovery from their source records.">
    <ResourceErrors resources={[{ label: "SLO alerts", error: alerts.error }, { label: "Deployments", error: deployments.error }, { label: "GitOps runs", error: gitopsRuns.error }, { label: "GitOps targets", error: gitopsTargets.error }, { label: "Service objectives", error: slos.error }, { label: "Services", error: services.error }]} />
    <div className="metric-grid metric-grid-4"><Metric label="Open signals" value={openCount} icon={<BellRing size={16} />} tone={openCount ? "red" : "green"} /><Metric label="SLO alerts" value={activeAlerts.length} icon={<Activity size={16} />} tone="amber" /><Metric label="Failed releases" value={failedDeployments.length} icon={<CircleAlert size={16} />} tone="red" /><Metric label="GitOps failures" value={troubledGitOpsRuns.length} icon={<AlertTriangle size={16} />} tone="violet" /></div>
    {(message || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || message}</div>}
    <section className="incident-context"><span className="incident-context-icon"><ShieldCheck size={16} /></span><span><strong>Operational signals from live platform data</strong><small>This view combines SLO alert records with failed deployment and GitOps runs. Alert acknowledgement and rollout retry update their source systems and audit trails.</small></span><Link to="/observability">Open observability <span>→</span></Link></section>
    <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Active response queue</h2><p>Unresolved SLO alerts and release failures in the selected environment.</p></div><button className="catalog-secondary-button" onClick={() => { alerts.refresh(); deployments.refresh(); gitopsRuns.refresh(); gitopsTargets.refresh(); slos.refresh(); }}>Refresh queue</button></div>
      {loading ? <div className="resource-loading"><span className="spinner" />Loading live response signals…</div> : openCount === 0 ? <div className="resource-empty"><div className="empty-state-icon"><ShieldCheck size={17} /></div><strong>No active reliability signals</strong><p>Active SLO alerts and release failures will appear here as they are recorded.</p></div> : <div className="incident-list">
        {activeAlerts.map((alert) => { const slo = sloMap.get(alert.slo_id); const service = slo ? serviceMap.get(slo.service_id) : null; return <article className="incident-row" key={`alert-${alert.id}`}><span className="incident-severity incident-high"><BellRing size={16} /></span><div className="incident-main"><div className="incident-title-row"><strong>{alert.summary}</strong><ResourceStatus value={alert.status} /></div><p>{service?.name || "Service"}{slo ? ` · ${slo.name}` : ""} · SLO evaluation indicates objective breach.</p><small>Opened {formatDate(alert.created_at)}{alert.acknowledged_at ? ` · Acknowledged ${formatDate(alert.acknowledged_at)}` : ""}</small></div>{alert.status === "firing" && canOperate && <button className="catalog-secondary-button" disabled={busy === alert.id} onClick={() => void acknowledge(alert)}><ShieldCheck size={13} /> Acknowledge</button>}</article>; })}
        {failedDeployments.map((deployment) => <article className="incident-row" key={`deployment-${deployment.id}`}><span className="incident-severity incident-medium"><CircleAlert size={16} /></span><div className="incident-main"><div className="incident-title-row"><strong>Release {deployment.version} failed</strong><ResourceStatus value={deployment.status} /></div><p>{serviceMap.get(deployment.service_id)?.name || "Service"} · {deployment.failure_message || deployment.failure_code || "Release run did not complete successfully."}</p><small>Updated {formatDate(deployment.updated_at)} · Attempt {deployment.attempt_count}/{deployment.max_attempts}</small></div><Link className="catalog-secondary-button" to="/deployments">View release</Link></article>)}
        {troubledGitOpsRuns.map((run) => <article className="incident-row" key={`gitops-${run.id}`}><span className="incident-severity incident-medium"><AlertTriangle size={16} /></span><div className="incident-main"><div className="incident-title-row"><strong>{run.application_name} {run.status === "blocked" ? "rollout blocked" : "rollout failed"}</strong><ResourceStatus value={run.status} /></div><p>{run.failure_message || run.failure_code || "GitOps reconciliation did not reach a healthy state."}</p><small>{run.action_kind.replaceAll("_", " ")} · Updated {formatDate(run.updated_at)}</small></div><div className="incident-actions">{run.status === "blocked" && canOperate && <button className="catalog-secondary-button" disabled={busy === run.id} onClick={() => void retryRun(run)}><RotateCcw size={13} /> Retry</button>}<Link className="catalog-secondary-button" to="/gitops">Open GitOps</Link></div></article>)}
      </div>}
    </section>
    <section className="incident-footer-note"><Activity size={14} /><span>Source of truth: SLO alert lifecycle, deployment state machine and GitOps run history. Incident records and response timelines are not represented as a separate data model yet.</span></section>
  </WorkspacePage>;
}

function Metric({ label, value, icon, tone }: { label: string; value: number; icon: React.ReactNode; tone: string }) { return <article className={`metric-card ${tone}`}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span></article>; }
