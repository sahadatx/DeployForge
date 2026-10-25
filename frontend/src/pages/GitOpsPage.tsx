import { useEffect, useMemo, useState } from "react";
import { GitBranch, GitCommitHorizontal, Plus, RotateCcw, X } from "lucide-react";
import { apiGet, apiPatch, apiPost, type ArgoApplicationStatus, type Environment, type GitOpsRun, type GitOpsTarget, type ServiceCatalog } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, useWorkspaceResource, WorkspacePage } from "../components/WorkspacePage";

export default function GitOpsPage() {
  const { workspaceId, environmentId: selectedEnvironmentId, canOperate } = usePlatformContext();
  const targets = useWorkspaceResource<GitOpsTarget[]>(workspaceId ? `/workspaces/${workspaceId}/gitops-targets` : null);
  const runs = useWorkspaceResource<GitOpsRun[]>(workspaceId ? `/workspaces/${workspaceId}/gitops-runs?limit=100` : null);
  const services = useWorkspaceResource<ServiceCatalog>(workspaceId ? `/workspaces/${workspaceId}/services?limit=100` : null, 60_000);
  const environments = useWorkspaceResource<Environment[]>(workspaceId ? `/workspaces/${workspaceId}/environments` : null, 60_000);
  const [providerStatuses, setProviderStatuses] = useState<Record<string, ArgoApplicationStatus>>({});
  const [showCreate, setShowCreate] = useState(false);
  const [serviceId, setServiceId] = useState("");
  const [environmentId, setEnvironmentId] = useState(selectedEnvironmentId);
  const [applicationName, setApplicationName] = useState("");
  const [project, setProject] = useState("default");
  const [cluster, setCluster] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const serviceMap = useMemo(() => new Map((services.data?.items || []).map((service) => [service.id, service])), [services.data]);
  const environmentMap = useMemo(() => new Map((environments.data || []).map((environment) => [environment.id, environment])), [environments.data]);
  const displayTargets = useMemo(() => (targets.data || []).filter((target) => !selectedEnvironmentId || target.environment_id === selectedEnvironmentId), [selectedEnvironmentId, targets.data]);
  const displayTargetIds = useMemo(() => new Set(displayTargets.map((target) => target.id)), [displayTargets]);
  const displayRuns = useMemo(() => (runs.data || []).filter((run) => !selectedEnvironmentId || Boolean(run.target_id && displayTargetIds.has(run.target_id))), [displayTargetIds, selectedEnvironmentId, runs.data]);

  useEffect(() => {
    if (!workspaceId || !targets.data?.length) { setProviderStatuses({}); return; }
    let active = true;
    const controller = new AbortController();
    const refreshStatuses = async () => {
      const pairs = await Promise.all(targets.data!.filter((target) => target.enabled).map(async (target) => {
        try {
          const status = await apiGet<ArgoApplicationStatus>(`/workspaces/${workspaceId}/gitops-targets/${target.id}/status`, controller.signal);
          return [target.id, status] as const;
        } catch (caught) {
          if (caught instanceof DOMException && caught.name === "AbortError") return null;
          return [target.id, { application_name: target.application_name, provider_status: "unavailable", failure_code: "status_unavailable", project: null, sync_status: null, health_status: null, operation_phase: null, revision: null, destination_name: null, destination_server: null }] as const;
        }
      }));
      if (active) setProviderStatuses(Object.fromEntries(pairs.filter((pair): pair is readonly [string, ArgoApplicationStatus] => pair !== null)));
    };
    void refreshStatuses();
    const timer = window.setInterval(() => void refreshStatuses(), 20_000);
    return () => { active = false; controller.abort(); window.clearInterval(timer); };
  }, [targets.data, workspaceId]);

  async function act(id: string, operation: () => Promise<unknown>, message: string) {
    setBusy(id); setError(""); setNotice("");
    try { await operation(); setNotice(message); targets.refresh(); runs.refresh(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "GitOps action failed."); }
    finally { setBusy(""); }
  }

  async function createTarget(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId) return;
    await act("new-target", async () => {
      await apiPost(`/workspaces/${workspaceId}/gitops-targets`, { service_id: serviceId, environment_id: environmentId, application_name: applicationName, expected_project: project, expected_cluster_name: cluster || undefined });
      setShowCreate(false); setServiceId(""); setEnvironmentId(""); setApplicationName(""); setCluster(""); setProject("default");
    }, "Argo CD application target registered.");
  }

  const failedRuns = displayRuns.filter((run) => ["failed", "blocked"].includes(run.status)).length;
  const healthyTargets = displayTargets.filter((target) => providerStatuses[target.id]?.health_status === "Healthy" && providerStatuses[target.id]?.sync_status === "Synced").length;

  return <WorkspacePage eyebrow="WORKSPACE" title="GitOps" description="Inspect Argo CD reconciliation, application health and durable deployment runs." actions={<button className="catalog-primary-button" onClick={() => setShowCreate((value) => !value)} disabled={!workspaceId || !canOperate}><Plus size={14} /> Register target</button>}>
    <ResourceErrors resources={[{ label: "GitOps targets", error: targets.error }, { label: "GitOps runs", error: runs.error }, { label: "Service catalog", error: services.error }, { label: "Environments", error: environments.error }]} />
    <div className="metric-grid metric-grid-3"><Metric label="Application targets" value={displayTargets.length} icon={<GitBranch size={16} />} tone="cyan" /><Metric label="Healthy & synced" value={healthyTargets} icon={<GitCommitHorizontal size={16} />} tone="green" /><Metric label="Failed / blocked runs" value={failedRuns} icon={<X size={16} />} tone="red" /></div>
    {(notice || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || notice}<button className="notice-dismiss" onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss message"><X size={14} /></button></div>}
    {showCreate && <section className="settings-panel resource-form-panel"><div className="settings-panel-heading"><div className="settings-panel-icon"><GitBranch size={16} /></div><div><h2>Register an Argo CD target</h2><p>Bind one catalog service and environment to a verified Argo CD Application.</p></div></div>{!canOperate ? <p className="permission-note">Platform engineer or workspace administrator access is required to manage GitOps targets.</p> : <form className="resource-form" onSubmit={(event) => void createTarget(event)}>
      <label>Service<select value={serviceId} onChange={(event) => setServiceId(event.target.value)} required><option value="">Select service</option>{services.data?.items.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select></label>
      <label>Environment<select value={environmentId} onChange={(event) => setEnvironmentId(event.target.value)} required><option value="">Select environment</option>{environments.data?.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)}</select></label>
      <label>Application name<input value={applicationName} onChange={(event) => setApplicationName(event.target.value)} placeholder="checkout-production" pattern="[a-z0-9]([a-z0-9.-]*[a-z0-9])?" maxLength={253} required /></label>
      <label>Argo CD project<input value={project} onChange={(event) => setProject(event.target.value)} required maxLength={80} /></label>
      <label>Expected cluster <span className="field-optional">optional</span><input value={cluster} onChange={(event) => setCluster(event.target.value)} placeholder="production-us-east-1" maxLength={180} /></label>
      <button className="settings-primary-button" disabled={busy === "new-target" || !serviceId || !environmentId}><Plus size={14} />{busy === "new-target" ? "Registering…" : "Register target"}</button>
    </form>}</section>}
    <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Application targets</h2><p>Provider status refreshes every 20 seconds; credentials remain server-side.</p></div><span className="live-indicator"><span className="status-dot" /> Argo CD status</span></div>
      <LoadingOrEmpty loading={targets.loading} empty={Boolean(targets.data && targets.data.length === 0)} label="No GitOps targets registered" />
      {selectedEnvironmentId && targets.data?.length && displayTargets.length === 0 ? <div className="resource-empty compact">No GitOps targets are bound to the selected environment.</div> : null}
      {displayTargets.length > 0 && <div className="resource-table-wrap"><table className="resource-table"><thead><tr><th>Application</th><th>Service / environment</th><th>Sync</th><th>Health</th><th>Revision / destination</th><th>Target</th><th /></tr></thead><tbody>{displayTargets.map((target) => {
        const status = providerStatuses[target.id];
        return <tr key={target.id}><td><span className="resource-cell-strong">{target.application_name}</span><small className="resource-subtext">Project {target.expected_project}</small></td><td><span className="resource-cell-strong">{serviceMap.get(target.service_id)?.name || "Service"}</span><small className="resource-subtext">{environmentMap.get(target.environment_id)?.name || "Environment"}</small></td><td><ResourceStatus value={status?.sync_status || status?.provider_status || "checking"} /></td><td><ResourceStatus value={status?.health_status || (status ? status.provider_status : "checking")} /></td><td><span className="resource-mono">{status?.revision?.slice(0, 12) || "—"}</span><small className="resource-subtext">{status?.destination_name || status?.destination_server || target.expected_cluster_name || "Destination pending"}</small></td><td><label className="toggle-inline"><input type="checkbox" checked={target.enabled} disabled={!canOperate || busy === target.id} onChange={(event) => void act(target.id, () => apiPatch(`/workspaces/${workspaceId}/gitops-targets/${target.id}`, { enabled: event.target.checked }), event.target.checked ? "GitOps target enabled." : "GitOps target disabled.")} /><span>{target.enabled ? "Enabled" : "Disabled"}</span></label></td><td><button className="catalog-secondary-button" onClick={() => targets.refresh()} title="Refresh Argo status"><GitCommitHorizontal size={13} /> Status</button></td></tr>;
      })}</tbody></table></div>}
    </section>
    <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Recent sync and rollback runs</h2><p>Release history connected to durable deployment and build records</p></div><button className="catalog-secondary-button" onClick={() => runs.refresh()}>Refresh runs</button></div>
      <LoadingOrEmpty loading={runs.loading} empty={Boolean(runs.data && runs.data.length === 0)} label="No GitOps runs recorded" />
      {selectedEnvironmentId && runs.data?.length && displayRuns.length === 0 ? <div className="resource-empty compact">No GitOps runs are linked to the selected environment.</div> : null}
      {displayRuns.length > 0 && <div className="resource-table-wrap"><table className="resource-table"><thead><tr><th>Application</th><th>Action</th><th>Desired revision</th><th>State</th><th>Started</th><th>Actions</th></tr></thead><tbody>{displayRuns.map((run) => <tr key={run.id}><td><span className="resource-cell-strong">{run.application_name}</span><small className="resource-subtext">Deployment {run.deployment_id.slice(0, 8)}</small></td><td>{run.action_kind.replaceAll("_", " ")}</td><td><span className="resource-mono">{run.desired_revision?.slice(0, 12) || "—"}</span><small className="resource-subtext">{run.artifact_digest?.slice(0, 20) || ""}</small></td><td><ResourceStatus value={run.status} />{run.failure_message && <small className="resource-subtext resource-failure">{run.failure_message}</small>}</td><td>{formatDate(run.started_at || run.created_at)}</td><td>{run.status === "blocked" && canOperate ? <button className="row-action" disabled={busy === run.id} onClick={() => void act(run.id, () => apiPost(`/workspaces/${workspaceId}/gitops-runs/${run.id}/retry`), "GitOps retry queued.")} title="Retry blocked run"><RotateCcw size={14} /></button> : <span className="muted-cell">—</span>}</td></tr>)}</tbody></table></div>}
    </section>
  </WorkspacePage>;
}

function Metric({ label, value, icon, tone }: { label: string; value: number; icon: React.ReactNode; tone: string }) { return <article className={`metric-card ${tone}`}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span></article>; }
