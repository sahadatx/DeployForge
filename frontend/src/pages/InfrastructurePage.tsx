import { useMemo, useState } from "react";
import { AlertTriangle, ClipboardList, GitBranch, Plus, ShieldCheck, X } from "lucide-react";
import { apiPost, type Environment, type InfrastructureRun, type InfrastructureTarget } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, useWorkspaceResource, WorkspacePage } from "../components/WorkspacePage";

export default function InfrastructurePage() {
  const { workspaceId, environmentId: selectedEnvironmentId, canOperate } = usePlatformContext();
  const targets = useWorkspaceResource<InfrastructureTarget[]>(workspaceId ? `/workspaces/${workspaceId}/infrastructure-targets` : null);
  const runs = useWorkspaceResource<InfrastructureRun[]>(workspaceId ? `/workspaces/${workspaceId}/infrastructure-runs?limit=100` : null);
  const environments = useWorkspaceResource<Environment[]>(workspaceId ? `/workspaces/${workspaceId}/environments` : null, 60_000);
  const [creating, setCreating] = useState(false);
  const [environmentId, setEnvironmentId] = useState(selectedEnvironmentId);
  const [repository, setRepository] = useState("");
  const [sourceRef, setSourceRef] = useState("main");
  const [directory, setDirectory] = useState(".");
  const [tool, setTool] = useState<"terraform" | "opentofu">("terraform");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const environmentMap = useMemo(() => new Map((environments.data || []).map((environment) => [environment.id, environment])), [environments.data]);
  const targetMap = useMemo(() => new Map((targets.data || []).map((target) => [target.environment_id, target])), [targets.data]);
  const displayEnvironments = useMemo(() => (environments.data || []).filter((environment) => !selectedEnvironmentId || environment.id === selectedEnvironmentId), [selectedEnvironmentId, environments.data]);
  const displayRuns = useMemo(() => (runs.data || []).filter((run) => !selectedEnvironmentId || run.environment_id === selectedEnvironmentId), [selectedEnvironmentId, runs.data]);

  async function act(id: string, operation: () => Promise<unknown>, message: string) {
    setBusy(id); setError(""); setNotice("");
    try { await operation(); setNotice(message); targets.refresh(); runs.refresh(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "The infrastructure action failed."); }
    finally { setBusy(""); }
  }

  async function createTarget(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId) return;
    await act("new-target", async () => {
      await apiPost(`/workspaces/${workspaceId}/infrastructure-targets`, { environment_id: environmentId, repository_url: repository, source_ref: sourceRef, working_directory: directory, tool });
      setCreating(false); setRepository(""); setSourceRef("main"); setDirectory("."); setEnvironmentId("");
    }, "Infrastructure target registered.");
  }

  async function queueRun(environment: Environment, runKind: "plan" | "drift") {
    await act(environment.id, () => apiPost(`/workspaces/${workspaceId}/environments/${environment.id}/infrastructure-runs`, { run_kind: runKind }), `${runKind === "plan" ? "Infrastructure plan" : "Drift check"} queued for ${environment.name}.`);
  }

  const awaitingApproval = displayRuns.filter((run) => run.status === "awaiting_approval").length;
  const driftFindings = displayRuns.filter((run) => run.run_kind === "drift" && run.status === "drift_detected").length;
  const destroys = displayRuns.filter((run) => (run.resource_destroys || 0) > 0).length;

  return <WorkspacePage eyebrow="WORKSPACE" title="Infrastructure" description="Plan and reconcile Terraform or OpenTofu changes with protected approval gates." actions={<button className="catalog-primary-button" onClick={() => setCreating((value) => !value)} disabled={!workspaceId || !canOperate}><Plus size={14} /> Register target</button>}>
    <ResourceErrors resources={[{ label: "Infrastructure targets", error: targets.error }, { label: "Infrastructure runs", error: runs.error }, { label: "Environments", error: environments.error }]} />
    <div className="metric-grid metric-grid-3"><Metric label="Automation targets" value={displayEnvironments.filter((environment) => targetMap.has(environment.id)).length} icon={<GitBranch size={16} />} tone="cyan" /><Metric label="Awaiting approval" value={awaitingApproval} icon={<ShieldCheck size={16} />} tone="amber" /><Metric label="Plans with destroys" value={destroys} icon={<AlertTriangle size={16} />} tone="red" /></div>
    {driftFindings > 0 && <div className="settings-notice is-error"><AlertTriangle size={14} />{driftFindings} drift check{driftFindings === 1 ? "" : "s"} reported changes.</div>}
    {(notice || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || notice}<button className="notice-dismiss" onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss message"><X size={14} /></button></div>}
    {creating && <section className="settings-panel resource-form-panel"><div className="settings-panel-heading"><div className="settings-panel-icon"><ClipboardList size={16} /></div><div><h2>Register infrastructure automation</h2><p>Targets accept a canonical GitHub HTTPS repository and a repository-relative working directory.</p></div></div>{!canOperate ? <p className="permission-note">Platform engineer or workspace administrator access is required.</p> : <form className="resource-form" onSubmit={(event) => void createTarget(event)}>
      <label>Environment<select required value={environmentId} onChange={(event) => setEnvironmentId(event.target.value)}><option value="">Select environment</option>{environments.data?.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)}</select></label>
      <label>GitHub repository<input type="url" required value={repository} onChange={(event) => setRepository(event.target.value)} placeholder="https://github.com/org/infrastructure" pattern="https://github\.com/[^/]+/[^/]+/?" /></label>
      <label>Source ref<input required value={sourceRef} onChange={(event) => setSourceRef(event.target.value)} placeholder="main" maxLength={255} /></label>
      <label>Working directory<input required value={directory} onChange={(event) => setDirectory(event.target.value)} placeholder="." maxLength={500} /></label>
      <label>Tool<select value={tool} onChange={(event) => setTool(event.target.value as "terraform" | "opentofu")}><option value="terraform">Terraform</option><option value="opentofu">OpenTofu</option></select></label>
      <button className="settings-primary-button" disabled={busy === "new-target" || !environmentId}><Plus size={14} />{busy === "new-target" ? "Registering…" : "Register target"}</button>
    </form>}</section>}
    <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Environment automation</h2><p>Queue plan or drift work against the selected environment target.</p></div><span className="live-indicator"><span className="status-dot" /> Durable workflow</span></div>
      <LoadingOrEmpty loading={environments.loading || targets.loading} empty={Boolean(environments.data && environments.data.length === 0)} label="Create an environment before configuring infrastructure" />
      {displayEnvironments.length > 0 && <div className="environment-grid">{displayEnvironments.map((environment) => {
        const target = targetMap.get(environment.id);
        return <article className="environment-card infrastructure-target-card" key={environment.id}><div className="environment-card-top"><span className="environment-symbol"><ClipboardList size={17} /></span><ResourceStatus value={target ? (target.enabled ? "enabled" : "disabled") : "not configured"} /><span className="target-tool">{target?.tool || "No provider"}</span></div><h2>{environment.name}</h2><p className="environment-slug">{target ? `${target.repository_url} · ${target.working_directory}` : "Register a Terraform or OpenTofu repository target."}</p><div className="environment-facts"><span>Source ref</span><strong>{target?.source_ref || "—"}</strong><span>Backend workspace</span><strong>{target?.backend_workspace || "Default"}</strong><span>Protection</span><strong>{environment.protected ? "Enabled" : "Disabled"}</strong></div><div className="infrastructure-actions"><button className="catalog-secondary-button" disabled={!canOperate || !target?.enabled || busy === environment.id} onClick={() => void queueRun(environment, "plan")}>Queue plan</button><button className="catalog-secondary-button" disabled={!canOperate || !target?.enabled || busy === environment.id} onClick={() => void queueRun(environment, "drift")}>Check drift</button></div></article>;
      })}</div>}
    </section>
    <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Plans, applies and drift checks</h2><p>Saved plan evidence must be reviewed by a different operator before apply.</p></div><button className="catalog-secondary-button" onClick={() => runs.refresh()}>Refresh</button></div>
      <LoadingOrEmpty loading={runs.loading} empty={Boolean(runs.data && runs.data.length === 0)} label="No infrastructure runs recorded" />
      {displayRuns.length > 0 && <div className="resource-table-wrap"><table className="resource-table"><thead><tr><th>Environment</th><th>Operation</th><th>Plan changes</th><th>State</th><th>Requested</th><th>Actions</th></tr></thead><tbody>{displayRuns.map((run) => <tr key={run.id}><td><span className="resource-cell-strong">{environmentMap.get(run.environment_id)?.name || "Environment"}</span><small className="resource-subtext">{run.source_ref} · {run.working_directory}</small></td><td>{run.run_kind} {run.active_operation ? `· ${run.active_operation}` : ""}</td><td><span className="plan-change-counts"><span>+{run.resource_additions ?? "—"}</span><span>~{run.resource_changes ?? "—"}</span><span className={run.resource_destroys ? "resource-failure" : ""}>−{run.resource_destroys ?? "—"}</span></span>{run.plan_digest && <small className="resource-subtext resource-mono">{run.plan_digest.slice(0, 20)}</small>}</td><td><ResourceStatus value={run.status} />{run.failure_message && <small className="resource-subtext resource-failure">{run.failure_message}</small>}{run.run_url && <a className="resource-inline-link" href={run.run_url} target="_blank" rel="noreferrer">Provider run ↗</a>}</td><td>{formatDate(run.created_at)}</td><td><div className="resource-row-actions">{run.status === "awaiting_approval" && canOperate ? <><button className="row-action approve" disabled={busy === run.id} onClick={() => void act(run.id, () => apiPost(`/workspaces/${workspaceId}/infrastructure-runs/${run.id}/approve`, { comment: "Reviewed in DeployForge." }), "Exact saved infrastructure plan approved for apply.")} title="Approve exact plan"><ShieldCheck size={14} /></button><button className="row-action reject" disabled={busy === run.id} onClick={() => void act(run.id, () => apiPost(`/workspaces/${workspaceId}/infrastructure-runs/${run.id}/reject`, { comment: "Rejected in DeployForge." }), "Infrastructure plan rejected.")} title="Reject plan"><X size={14} /></button></> : run.status === "blocked" && canOperate ? <button className="row-action" disabled={busy === run.id} onClick={() => void act(run.id, () => apiPost(`/workspaces/${workspaceId}/infrastructure-runs/${run.id}/retry`), "Infrastructure retry queued.")} title="Retry blocked run"><ClipboardList size={14} /></button> : <span className="muted-cell">—</span>}</div></td></tr>)}</tbody></table></div>}
    </section>
  </WorkspacePage>;
}

function Metric({ label, value, icon, tone }: { label: string; value: number; icon: React.ReactNode; tone: string }) { return <article className={`metric-card ${tone}`}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span></article>; }
