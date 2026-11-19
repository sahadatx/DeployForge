import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ClipboardList, GitBranch, Plus, ShieldCheck, X } from "lucide-react";
import { apiPost, type Environment, type InfrastructureRun, type InfrastructureTarget } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { FieldLabel, FormDrawer } from "../components/FormDrawer";
import { RecordDrawer, ViewAction, type RecordDetails } from "../components/RecordDrawer";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, TablePagination, TableToolbar, useTableView, useWorkspaceResource, WorkspacePage } from "../components/WorkspacePage";

export default function InfrastructurePage() {
  const { workspaceId, environmentId: selectedEnvironmentId, canOperate, session } = usePlatformContext();
  const targets = useWorkspaceResource<InfrastructureTarget[]>(workspaceId ? `/workspaces/${workspaceId}/infrastructure-targets` : null);
  const runs = useWorkspaceResource<InfrastructureRun[]>(workspaceId ? `/workspaces/${workspaceId}/infrastructure-runs?limit=100` : null, 20_000, "array");
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
  const [selected, setSelected] = useState<{ kind: "target" | "run"; id: string } | null>(null);
  const environmentMap = useMemo(() => new Map((environments.data || []).map((environment) => [environment.id, environment])), [environments.data]);
  const targetMap = useMemo(() => new Map((targets.data || []).map((target) => [target.environment_id, target])), [targets.data]);
  const displayEnvironments = useMemo(() => (environments.data || []).filter((environment) => !selectedEnvironmentId || environment.id === selectedEnvironmentId), [selectedEnvironmentId, environments.data]);
  const displayRuns = useMemo(() => (runs.data || []).filter((run) => !selectedEnvironmentId || run.environment_id === selectedEnvironmentId), [selectedEnvironmentId, runs.data]);
  const scopeKey = `${workspaceId}:${selectedEnvironmentId}`;
  const environmentsTable = useTableView(displayEnvironments, (environment) => {
    const target = targetMap.get(environment.id);
    return `${environment.name} ${environment.slug} ${target?.repository_url || ""} ${target?.source_ref || ""} ${target?.working_directory || ""} ${target?.tool || ""}`;
  }, {
    resetKey: scopeKey,
    filters: [
      { id: "configuration", label: "Configuration", getValue: (environment) => targetMap.has(environment.id) ? "configured" : "not_configured", options: [{ value: "configured", label: "Configured targets" }, { value: "not_configured", label: "Not configured" }] },
      { id: "enabled", label: "Target state", getValue: (environment) => { const target = targetMap.get(environment.id); return target ? (target.enabled ? "enabled" : "disabled") : "not_configured"; } },
      { id: "tool", label: "Tool", getValue: (environment) => targetMap.get(environment.id)?.tool },
    ],
  });
  const runsTable = useTableView(displayRuns, (run) => `${environmentMap.get(run.environment_id)?.name || ""} ${run.run_kind} ${run.active_operation || ""} ${run.status} ${run.source_ref} ${run.working_directory} ${run.failure_message || ""}`, {
    resetKey: scopeKey,
    filters: [
      { id: "status", label: "Run state", getValue: (run) => run.status, options: Array.from(new Set(["awaiting_approval", ...displayRuns.map((run) => run.status)])).sort().map((value) => ({ value, label: value.replaceAll("_", " ") })) },
      { id: "kind", label: "Run type", getValue: (run) => run.run_kind },
      { id: "operation", label: "Operation", getValue: (run) => run.active_operation },
      { id: "destroys", label: "Destroy changes", getValue: (run) => run.resource_destroys == null ? "unknown" : run.resource_destroys > 0 ? "destroys" : "none", options: [{ value: "destroys", label: "With destroys" }, { value: "none", label: "Without destroys" }, { value: "unknown", label: "Not reported" }] },
    ],
  });

  useEffect(() => { setSelected(null); }, [scopeKey]);
  useEffect(() => { setCreating(false); resetTargetForm(); }, [workspaceId]);

  const selectedTarget = selected?.kind === "target" ? targets.data?.find((target) => target.id === selected.id && displayEnvironments.some((environment) => environment.id === target.environment_id)) : undefined;
  const selectedRun = selected?.kind === "run" ? displayRuns.find((run) => run.id === selected.id) : undefined;
  const record: RecordDetails | null = selectedTarget ? {
    title: `${environmentMap.get(selectedTarget.environment_id)?.name || "Environment"} automation`,
    eyebrow: "INFRASTRUCTURE TARGET",
    status: selectedTarget.enabled ? "enabled" : "disabled",
    sections: [{ title: "Target configuration", fields: [
      { label: "Target ID", value: selectedTarget.id, mono: true },
      { label: "Environment", value: environmentMap.get(selectedTarget.environment_id)?.name || selectedTarget.environment_id },
      { label: "Tool", value: selectedTarget.tool },
      { label: "Repository", value: selectedTarget.repository_url, mono: true },
      { label: "Source ref", value: selectedTarget.source_ref, mono: true },
      { label: "Working directory", value: selectedTarget.working_directory, mono: true },
      { label: "Backend workspace", value: selectedTarget.backend_workspace },
      { label: "Created", value: formatDate(selectedTarget.created_at) },
      { label: "Updated", value: formatDate(selectedTarget.updated_at) },
    ] }],
  } : selectedRun ? {
    title: `${environmentMap.get(selectedRun.environment_id)?.name || "Environment"} · ${selectedRun.run_kind}`,
    eyebrow: "INFRASTRUCTURE RUN",
    status: selectedRun.status,
    sections: [
      { title: "Run identity", fields: [
        { label: "Run ID", value: selectedRun.id, mono: true },
        { label: "Environment", value: environmentMap.get(selectedRun.environment_id)?.name || selectedRun.environment_id },
        { label: "Target ID", value: selectedRun.target_id, mono: true },
        { label: "Run type", value: selectedRun.run_kind },
        { label: "Active operation", value: selectedRun.active_operation },
        { label: "Requested by", value: selectedRun.requested_by, mono: true },
        { label: "Approved by", value: selectedRun.approved_by, mono: true },
        { label: "Attempt number", value: selectedRun.attempt_number },
        { label: "Attempts used", value: `${selectedRun.attempt_count}/${selectedRun.max_attempts}` },
      ] },
      { title: "Saved plan evidence", fields: [
        { label: "Source ref", value: selectedRun.source_ref, mono: true },
        { label: "Working directory", value: selectedRun.working_directory, mono: true },
        { label: "Plan digest", value: selectedRun.plan_digest, mono: true },
        { label: "Plan artifact URI", value: selectedRun.plan_artifact_uri, mono: true },
        { label: "Plan has changes", value: selectedRun.plan_has_changes == null ? null : selectedRun.plan_has_changes ? "Yes" : "No" },
        { label: "Additions", value: selectedRun.resource_additions },
        { label: "Changes", value: selectedRun.resource_changes },
        { label: "Destroys", value: selectedRun.resource_destroys },
        { label: "Quota usage", value: Object.keys(selectedRun.quota_usage || {}).length ? JSON.stringify(selectedRun.quota_usage, null, 2) : null, mono: true },
      ] },
      { title: "Provider and lifecycle", fields: [
        { label: "External run ID", value: selectedRun.external_run_id, mono: true },
        { label: "Provider run URL", value: selectedRun.run_url, mono: true },
        { label: "Created", value: formatDate(selectedRun.created_at) },
        { label: "Started", value: formatDate(selectedRun.started_at) },
        { label: "Finished", value: formatDate(selectedRun.finished_at) },
        { label: "Updated", value: formatDate(selectedRun.updated_at) },
        { label: "Callback deadline", value: formatDate(selectedRun.callback_deadline_at) },
        { label: "Failure code", value: selectedRun.failure_code, mono: true },
        { label: "Failure message", value: selectedRun.failure_message },
      ] },
    ],
  } : null;

  function showAutomationTargets() {
    environmentsTable.resetFilters(); environmentsTable.setFilterValue("configuration", "configured");
    document.getElementById("infrastructure-targets")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function showRunCategory(id: "status" | "destroys", value: string) {
    runsTable.resetFilters(); runsTable.setFilterValue(id, value);
    document.getElementById("infrastructure-runs")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function resetTargetForm() {
    setEnvironmentId(selectedEnvironmentId); setRepository(""); setSourceRef("main"); setDirectory("."); setTool("terraform"); setError("");
  }

  async function act(id: string, operation: () => Promise<unknown>, message: string) {
    if (busy) return;
    setBusy(id); setError(""); setNotice("");
    try { await operation(); setNotice(message); targets.refresh(); runs.refresh(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "The infrastructure action failed."); }
    finally { setBusy(""); }
  }

  async function createTarget(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || busy === "new-target") return;
    const cleanedRef = sourceRef.trim();
    const cleanedDirectory = directory.trim();
    if (!cleanedRef || /\s|[\u0000-\u001f]/u.test(cleanedRef)) { setError("Source ref cannot be blank or contain whitespace or control characters."); return; }
    if (cleanedDirectory.startsWith("/") || cleanedDirectory.includes("\\") || cleanedDirectory.split("/").some((part) => part === ".." || part === ".git")) { setError("Working directory must be a relative path inside the repository, without parent or .git directories."); return; }
    try {
      const url = new URL(repository.trim());
      if (url.protocol !== "https:" || url.hostname !== "github.com" || url.username || url.password || url.search || url.hash || url.pathname.replace(/^\/|\/$/g, "").split("/").length !== 2) throw new Error();
    } catch { setError("Enter a canonical HTTPS GitHub repository URL, such as https://github.com/organization/repository."); return; }
    await act("new-target", async () => {
      await apiPost(`/workspaces/${workspaceId}/infrastructure-targets`, { environment_id: environmentId, repository_url: repository.trim(), source_ref: cleanedRef, working_directory: cleanedDirectory, tool });
      setCreating(false); resetTargetForm();
    }, "Infrastructure target registered.");
  }

  async function queueRun(environment: Environment, runKind: "plan" | "drift") {
    await act(environment.id, () => apiPost(`/workspaces/${workspaceId}/environments/${environment.id}/infrastructure-runs`, { run_kind: runKind }), `${runKind === "plan" ? "Infrastructure plan" : "Drift check"} queued for ${environment.name}.`);
  }

  const awaitingApproval = displayRuns.filter((run) => run.status === "awaiting_approval").length;
  const driftFindings = displayRuns.filter((run) => run.run_kind === "drift" && run.status === "drift_detected").length;
  const destroys = displayRuns.filter((run) => (run.resource_destroys || 0) > 0).length;

  return <WorkspacePage eyebrow="WORKSPACE" title="Infrastructure" description="Plan and reconcile Terraform or OpenTofu changes with protected approval gates." actions={<button className="catalog-primary-button" onClick={() => { resetTargetForm(); setCreating(true); }} disabled={!workspaceId || !canOperate || Boolean(busy)}><Plus size={14} /> Register target</button>}>
    <ResourceErrors resources={[{ label: "Infrastructure targets", error: targets.error }, { label: "Infrastructure runs", error: runs.error }, { label: "Environments", error: environments.error }]} />
    <div className="metric-grid metric-grid-3"><Metric label="Automation targets" value={displayEnvironments.filter((environment) => targetMap.has(environment.id)).length} icon={<GitBranch size={16} />} tone="cyan" onClick={showAutomationTargets} active={environmentsTable.filters.find((filter) => filter.id === "configuration")?.value === "configured"} /><Metric label="Awaiting approval" value={awaitingApproval} icon={<ShieldCheck size={16} />} tone="amber" onClick={() => showRunCategory("status", "awaiting_approval")} active={runsTable.filters.find((filter) => filter.id === "status")?.value === "awaiting_approval"} /><Metric label="Plans with destroys" value={destroys} icon={<AlertTriangle size={16} />} tone="red" onClick={() => showRunCategory("destroys", "destroys")} active={runsTable.filters.find((filter) => filter.id === "destroys")?.value === "destroys"} /></div>
    {driftFindings > 0 && <div className="settings-notice is-error"><AlertTriangle size={14} />{driftFindings} drift check{driftFindings === 1 ? "" : "s"} reported changes.</div>}
    {(notice || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || notice}<button className="notice-dismiss" onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss message"><X size={14} /></button></div>}
    {creating && <FormDrawer title="Register infrastructure automation" description="Targets accept a canonical GitHub HTTPS repository and a repository-relative working directory." dirty={Boolean(environmentId !== selectedEnvironmentId || repository || sourceRef !== "main" || directory !== "." || tool !== "terraform")} onClose={() => { resetTargetForm(); setCreating(false); }} onReset={resetTargetForm} busy={busy === "new-target"} error={error} actions={canOperate && <button form="create-infrastructure-target-form" type="submit" className="settings-primary-button" disabled={busy === "new-target" || !environmentId}><Plus size={14} />{busy === "new-target" ? "Registering…" : "Register target"}</button>}>{!canOperate ? <p className="permission-note">Platform engineer or workspace administrator access is required.</p> : <form id="create-infrastructure-target-form" className="resource-form" onSubmit={(event) => void createTarget(event)}>
      <label><FieldLabel required>Environment</FieldLabel><select required value={environmentId} onChange={(event) => setEnvironmentId(event.target.value)}><option value="">Select environment</option>{environments.data?.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)}</select></label>
      <label><FieldLabel required>GitHub repository</FieldLabel><input type="url" required value={repository} onChange={(event) => setRepository(event.target.value)} placeholder="https://github.com/org/infrastructure" maxLength={500} pattern="https://github\.com/[^/]+/[^/]+/?" /></label>
      <label><FieldLabel required>Source ref</FieldLabel><input required value={sourceRef} onChange={(event) => setSourceRef(event.target.value)} placeholder="main" maxLength={255} /></label>
      <label><FieldLabel required>Working directory</FieldLabel><input required value={directory} onChange={(event) => setDirectory(event.target.value)} placeholder="." maxLength={500} /></label>
      <label><FieldLabel required>Tool</FieldLabel><select required value={tool} onChange={(event) => setTool(event.target.value as "terraform" | "opentofu")}><option value="terraform">Terraform</option><option value="opentofu">OpenTofu</option></select></label>
    </form>}</FormDrawer>}
    <section id="infrastructure-targets" className="resource-panel"><div className="resource-panel-heading"><div><h2>Environment automation</h2><p>Queue plan or drift work against the selected environment target.</p></div><span className="live-indicator"><span className="status-dot" /> Durable workflow</span></div>
      <LoadingOrEmpty loading={environments.loading || targets.loading} empty={Boolean(environments.data && environments.data.length === 0)} label="Create an environment before configuring infrastructure" />
      {displayEnvironments.length > 0 && <TableToolbar query={environmentsTable.query} onQueryChange={environmentsTable.setQuery} filteredCount={environmentsTable.filteredItems.length} totalCount={environmentsTable.totalItems} pageSize={environmentsTable.pageSize} onPageSizeChange={environmentsTable.setPageSize} filters={environmentsTable.filters} onFilterChange={environmentsTable.setFilterValue} onResetFilters={environmentsTable.resetFilters} isFiltering={environmentsTable.isFiltering} label="automation environments" />}
      {selectedEnvironmentId && environments.data?.length && displayEnvironments.length === 0 ? <div className="resource-empty compact">The selected environment is unavailable.</div> : null}
      {displayEnvironments.length > 0 && environmentsTable.filteredItems.length === 0 && <div className="resource-empty compact">No automation environments match these filters.</div>}
      {environmentsTable.pageItems.length > 0 && <div className="environment-grid">{environmentsTable.pageItems.map((environment) => {
        const target = targetMap.get(environment.id);
        return <article className="environment-card infrastructure-target-card" key={environment.id}><div className="environment-card-top"><span className="environment-symbol"><ClipboardList size={17} /></span><ResourceStatus value={target ? (target.enabled ? "enabled" : "disabled") : "not configured"} /><span className="target-tool">{target?.tool || "No provider"}</span></div><h2>{environment.name}</h2><p className="environment-slug">{target ? `${target.repository_url} · ${target.working_directory}` : "Register a Terraform or OpenTofu repository target."}</p><div className="environment-facts"><span>Source ref</span><strong>{target?.source_ref || "—"}</strong><span>Backend workspace</span><strong>{target?.backend_workspace || "Default"}</strong><span>Protection</span><strong>{environment.protected ? "Enabled" : "Disabled"}</strong></div><div className="infrastructure-actions">{target && <ViewAction label={`infrastructure target for ${environment.name}`} onClick={() => setSelected({ kind: "target", id: target.id })} />}<button className="catalog-secondary-button" disabled={!canOperate || !target?.enabled || Boolean(busy)} onClick={() => void queueRun(environment, "plan")}>Queue plan</button><button className="catalog-secondary-button" disabled={!canOperate || !target?.enabled || Boolean(busy)} onClick={() => void queueRun(environment, "drift")}>Check drift</button></div></article>;
      })}</div>}
      {displayEnvironments.length > 0 && <TablePagination firstItem={environmentsTable.firstItem} lastItem={environmentsTable.lastItem} totalItems={environmentsTable.filteredItems.length} page={environmentsTable.page} pageCount={environmentsTable.pageCount} onPageChange={environmentsTable.setPage} label="automation environments" />}
    </section>
    <section id="infrastructure-runs" className="resource-panel"><div className="resource-panel-heading"><div><h2>Plans, applies and drift checks</h2><p>Saved plan evidence must be reviewed by a different operator before apply.</p></div><button className="catalog-secondary-button" onClick={() => runs.refresh()}>Refresh</button></div>
      <LoadingOrEmpty loading={runs.loading} empty={Boolean(runs.data && runs.data.length === 0)} label="No infrastructure runs recorded" />
      {displayRuns.length > 0 && <TableToolbar query={runsTable.query} onQueryChange={runsTable.setQuery} filteredCount={runsTable.filteredItems.length} totalCount={runsTable.totalItems} pageSize={runsTable.pageSize} onPageSizeChange={runsTable.setPageSize} filters={runsTable.filters} onFilterChange={runsTable.setFilterValue} onResetFilters={runsTable.resetFilters} isFiltering={runsTable.isFiltering} label="infrastructure runs" />}
      {selectedEnvironmentId && runs.data?.length && displayRuns.length === 0 ? <div className="resource-empty compact">No infrastructure runs are linked to the selected environment.</div> : null}
      {displayRuns.length > 0 && runsTable.filteredItems.length === 0 && <div className="resource-empty compact">No infrastructure runs match this filter.</div>}
      {runsTable.pageItems.length > 0 && <div className="resource-table-wrap"><table className="resource-table has-actions"><caption className="sr-only">Infrastructure plans, applies and drift checks with saved plan changes and review actions</caption><thead><tr><th scope="col">Environment</th><th scope="col">Operation</th><th scope="col">Plan changes</th><th scope="col">State</th><th scope="col">Requested</th><th scope="col">Actions</th></tr></thead><tbody>{runsTable.pageItems.map((run) => <tr key={run.id}><td><span className="resource-cell-strong">{environmentMap.get(run.environment_id)?.name || "Environment"}</span><small className="resource-subtext">{run.source_ref} · {run.working_directory}</small></td><td>{run.run_kind} {run.active_operation ? `· ${run.active_operation}` : ""}</td><td><span className="plan-change-counts"><span>+{run.resource_additions ?? "—"}</span><span>~{run.resource_changes ?? "—"}</span><span className={run.resource_destroys ? "resource-failure" : ""}>−{run.resource_destroys ?? "—"}</span></span>{run.plan_digest && <small className="resource-subtext resource-mono">{run.plan_digest.slice(0, 20)}</small>}</td><td><ResourceStatus value={run.status} />{run.failure_message && <small className="resource-subtext resource-failure">{run.failure_message}</small>}{run.run_url && <a className="resource-inline-link" href={run.run_url} target="_blank" rel="noreferrer">Provider run ↗</a>}</td><td>{formatDate(run.created_at)}</td><td><div className="resource-row-actions"><ViewAction label={`infrastructure run ${run.id.slice(0, 8)}`} onClick={() => setSelected({ kind: "run", id: run.id })} />{run.status === "awaiting_approval" && canOperate && run.requested_by !== session.user.id ? <><button className="row-action approve" disabled={Boolean(busy)} onClick={() => void act(run.id, () => apiPost(`/workspaces/${workspaceId}/infrastructure-runs/${run.id}/approve`, { comment: "Reviewed in DeployForge." }), "Exact saved infrastructure plan approved for apply.")} title="Approve exact plan"><ShieldCheck size={14} /></button><button className="row-action reject" disabled={Boolean(busy)} onClick={() => void act(run.id, () => apiPost(`/workspaces/${workspaceId}/infrastructure-runs/${run.id}/reject`, { comment: "Rejected in DeployForge." }), "Infrastructure plan rejected.")} title="Reject plan"><X size={14} /></button></> : run.status === "awaiting_approval" && run.requested_by === session.user.id ? <span className="approval-hint"><ShieldCheck size={12} /> Needs another approver</span> : run.status === "blocked" && canOperate ? <button className="row-action" disabled={Boolean(busy)} onClick={() => void act(run.id, () => apiPost(`/workspaces/${workspaceId}/infrastructure-runs/${run.id}/retry`), "Infrastructure retry queued.")} title="Retry blocked run"><ClipboardList size={14} /></button> : null}</div></td></tr>)}</tbody></table><TablePagination firstItem={runsTable.firstItem} lastItem={runsTable.lastItem} totalItems={runsTable.filteredItems.length} page={runsTable.page} pageCount={runsTable.pageCount} onPageChange={runsTable.setPage} label="infrastructure runs" /></div>}
    </section>
    {record && <RecordDrawer record={record} onClose={() => setSelected(null)} />}
  </WorkspacePage>;
}

function Metric({ label, value, icon, tone, onClick, active = false }: { label: string; value: number; icon: React.ReactNode; tone: string; onClick: () => void; active?: boolean }) { return <button type="button" className={`metric-card metric-filter-card ${tone}${active ? " is-active" : ""}`} onClick={onClick} aria-pressed={active} title={`Show ${label.toLowerCase()}`}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span></button>; }
