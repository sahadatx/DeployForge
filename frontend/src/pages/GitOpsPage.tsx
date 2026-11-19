import { useEffect, useMemo, useState } from "react";
import { GitBranch, GitCommitHorizontal, Plus, RotateCcw, X } from "lucide-react";
import { apiGet, apiPatch, apiPost, type Environment, type GitOpsRun, type GitOpsRunDetail, type GitOpsRolloutAction, type GitOpsTarget, type GitOpsTargetStatus, type ServiceCatalog } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { FieldLabel, FormDrawer } from "../components/FormDrawer";
import { RecordDrawer, ViewAction, type RecordDetails } from "../components/RecordDrawer";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, TablePagination, TableToolbar, useTableView, useWorkspaceResource, WorkspacePage } from "../components/WorkspacePage";

export default function GitOpsPage() {
  const { workspaceId, environmentId: selectedEnvironmentId, canOperate } = usePlatformContext();
  const targets = useWorkspaceResource<GitOpsTarget[]>(workspaceId ? `/workspaces/${workspaceId}/gitops-targets` : null);
  const runs = useWorkspaceResource<GitOpsRun[]>(workspaceId ? `/workspaces/${workspaceId}/gitops-runs?limit=100` : null, 20_000, "array");
  const services = useWorkspaceResource<ServiceCatalog>(workspaceId ? `/workspaces/${workspaceId}/services?limit=100` : null, 60_000, "catalog");
  const environments = useWorkspaceResource<Environment[]>(workspaceId ? `/workspaces/${workspaceId}/environments` : null, 60_000);
  const [providerStatuses, setProviderStatuses] = useState<Record<string, GitOpsTargetStatus>>({});
  const [showCreate, setShowCreate] = useState(false);
  const [serviceId, setServiceId] = useState("");
  const [environmentId, setEnvironmentId] = useState(selectedEnvironmentId);
  const [applicationName, setApplicationName] = useState("");
  const [provider, setProvider] = useState<"argocd" | "flux">("argocd");
  const [reconciliationNamespace, setReconciliationNamespace] = useState("");
  const [project, setProject] = useState("default");
  const [cluster, setCluster] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [runDetail, setRunDetail] = useState<GitOpsRunDetail | null>(null);
  const [selected, setSelected] = useState<{ kind: "target" | "run"; id: string } | null>(null);
  const serviceMap = useMemo(() => new Map((services.data?.items || []).map((service) => [service.id, service])), [services.data]);
  const environmentMap = useMemo(() => new Map((environments.data || []).map((environment) => [environment.id, environment])), [environments.data]);
  const displayTargets = useMemo(() => (targets.data || []).filter((target) => !selectedEnvironmentId || target.environment_id === selectedEnvironmentId), [selectedEnvironmentId, targets.data]);
  const displayTargetIds = useMemo(() => new Set(displayTargets.map((target) => target.id)), [displayTargets]);
  const displayRuns = useMemo(() => (runs.data || []).filter((run) => !selectedEnvironmentId || Boolean(run.target_id && displayTargetIds.has(run.target_id))), [displayTargetIds, selectedEnvironmentId, runs.data]);
  const scopeKey = `${workspaceId}:${selectedEnvironmentId}`;
  const targetsTable = useTableView(displayTargets, (target) => `${target.application_name} ${target.provider} ${target.expected_project} ${target.reconciliation_namespace || ""} ${serviceMap.get(target.service_id)?.name || ""} ${environmentMap.get(target.environment_id)?.name || ""} ${target.expected_cluster_name || ""} ${target.enabled ? "enabled" : "disabled"} ${providerStatuses[target.id]?.run_status || ""} ${providerStatuses[target.id]?.sync_status || ""} ${providerStatuses[target.id]?.health_status || ""}`, {
    resetKey: scopeKey,
    filters: [
      { id: "enabled", label: "Target state", getValue: (target) => target.enabled ? "enabled" : "disabled" },
      { id: "sync", label: "Sync status", getValue: (target) => target.enabled ? providerStatuses[target.id]?.sync_status || providerStatuses[target.id]?.run_status || providerStatuses[target.id]?.provider_status || "checking" : "disabled", options: Array.from(new Set(["Synced", "disabled", ...displayTargets.map((target) => target.enabled ? providerStatuses[target.id]?.sync_status || providerStatuses[target.id]?.run_status || providerStatuses[target.id]?.provider_status || "checking" : "disabled")])).sort().map((value) => ({ value, label: value.replaceAll("_", " ") })) },
      { id: "health", label: "Health status", getValue: (target) => target.enabled ? providerStatuses[target.id]?.health_status || providerStatuses[target.id]?.run_status || providerStatuses[target.id]?.provider_status || "checking" : "disabled", options: Array.from(new Set(["Healthy", "disabled", ...displayTargets.map((target) => target.enabled ? providerStatuses[target.id]?.health_status || providerStatuses[target.id]?.run_status || providerStatuses[target.id]?.provider_status || "checking" : "disabled")])).sort().map((value) => ({ value, label: value.replaceAll("_", " ") })) },
    ],
  });
  const runsTable = useTableView(displayRuns, (run) => `${run.application_name} ${run.action_kind} ${run.desired_revision || ""} ${run.status} ${run.failure_message || ""}`, {
    resetKey: scopeKey,
    filters: [
      { id: "status", label: "Run state", getValue: (run) => run.status },
      { id: "action", label: "Action", getValue: (run) => run.action_kind },
      { id: "outcome", label: "Run outcome", getValue: (run) => ["failed", "blocked"].includes(run.status) ? "needs_attention" : "other", options: [{ value: "needs_attention", label: "Failed or blocked" }, { value: "other", label: "Other states" }] },
    ],
  });

  useEffect(() => { setSelected(null); }, [scopeKey]);
  useEffect(() => { setShowCreate(false); resetTargetForm(); }, [workspaceId]);
  useEffect(() => {
    setRunDetail(null);
    if (!workspaceId || selected?.kind !== "run") return;
    let active = true;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const detail = await apiGet<GitOpsRunDetail>(
          `/workspaces/${workspaceId}/gitops-runs/${selected.id}`,
          controller.signal,
        );
        if (active) setRunDetail(detail);
      } catch (caught) {
        if (active && !(caught instanceof DOMException && caught.name === "AbortError")) {
          setError(caught instanceof Error ? caught.message : "Could not refresh rollout details.");
        }
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10_000);
    return () => { active = false; controller.abort(); window.clearInterval(timer); };
  }, [selected?.id, selected?.kind, workspaceId]);

  const selectedTarget = selected?.kind === "target" ? displayTargets.find((target) => target.id === selected.id) : undefined;
  const selectedRunList = selected?.kind === "run" ? displayRuns.find((run) => run.id === selected.id) : undefined;
  const selectedRun = selected?.kind === "run" && runDetail?.id === selected.id ? runDetail : selectedRunList;
  const formEnvironment = environmentMap.get(environmentId);
  const record: RecordDetails | null = selectedTarget ? {
    title: selectedTarget.application_name,
    eyebrow: "GITOPS TARGET",
    description: "GitOps target configuration and the latest recorded provider response.",
    status: selectedTarget.enabled ? "enabled" : "disabled",
    sections: [
      { title: "Target configuration", fields: [
        { label: "Target ID", value: selectedTarget.id, mono: true },
        { label: "Service", value: serviceMap.get(selectedTarget.service_id)?.name || selectedTarget.service_id },
        { label: "Environment", value: environmentMap.get(selectedTarget.environment_id)?.name || selectedTarget.environment_id },
        { label: "Provider", value: selectedTarget.provider === "flux" ? "Flux" : "Argo CD" },
        { label: "Expected project", value: selectedTarget.provider === "argocd" ? selectedTarget.expected_project : undefined },
        { label: "Expected cluster", value: selectedTarget.expected_cluster_name },
        { label: "Kustomization namespace", value: selectedTarget.reconciliation_namespace },
        { label: "Artifact name", value: selectedTarget.artifact_name },
        { label: "Created", value: formatDate(selectedTarget.created_at) },
        { label: "Updated", value: formatDate(selectedTarget.updated_at) },
      ] },
      { title: "Provider response", fields: [
        { label: "Provider status", value: providerStatuses[selectedTarget.id]?.provider_status },
        { label: "Latest run", value: providerStatuses[selectedTarget.id]?.run_status },
        { label: "Project", value: providerStatuses[selectedTarget.id]?.project },
        { label: "Sync status", value: providerStatuses[selectedTarget.id]?.sync_status },
        { label: "Health status", value: providerStatuses[selectedTarget.id]?.health_status },
        { label: "Operation phase", value: providerStatuses[selectedTarget.id]?.operation_phase },
        { label: "Revision", value: providerStatuses[selectedTarget.id]?.revision, mono: true },
        { label: "Destination name", value: providerStatuses[selectedTarget.id]?.destination_name },
        { label: "Destination server", value: providerStatuses[selectedTarget.id]?.destination_server, mono: true },
        { label: "Namespace", value: providerStatuses[selectedTarget.id]?.namespace },
        { label: "Failure code", value: providerStatuses[selectedTarget.id]?.failure_code, mono: true },
      ] },
    ],
  } : selectedRun ? {
    title: `${selectedRun.application_name} · ${selectedRun.action_kind.replaceAll("_", " ")}`,
    eyebrow: "GITOPS RUN",
    status: selectedRun.status,
    sections: [
      { title: "Run identity", fields: [
        { label: "Run ID", value: selectedRun.id, mono: true },
        { label: "Deployment ID", value: selectedRun.deployment_id, mono: true },
        { label: "Build run ID", value: selectedRun.build_run_id, mono: true },
        { label: "Target ID", value: selectedRun.target_id, mono: true },
        { label: "Rollback source run", value: selectedRun.rollback_from_run_id, mono: true },
        { label: "Attempt number", value: selectedRun.attempt_number },
        { label: "Attempts used", value: `${selectedRun.attempt_count}/${selectedRun.max_attempts}` },
      ] },
      { title: "Reconciliation evidence", fields: [
        { label: "Desired revision", value: selectedRun.desired_revision, mono: true },
        { label: "Observed revision", value: selectedRun.observed_revision, mono: true },
        { label: "Artifact URI", value: selectedRun.artifact_uri, mono: true },
        { label: "Artifact digest", value: selectedRun.artifact_digest, mono: true },
        { label: "Sync status", value: selectedRun.sync_status },
        { label: "Health status", value: selectedRun.health_status },
        { label: "Operation phase", value: selectedRun.operation_phase },
        { label: "Destination name", value: selectedRun.destination_name },
        { label: "Destination server", value: selectedRun.destination_server, mono: true },
        { label: "Rollout phase", value: selectedRun.rollout_snapshot?.phase },
        { label: "Canary stage", value: selectedRun.rollout_snapshot?.current_step_index === null || selectedRun.rollout_snapshot?.current_step_index === undefined ? null : `${selectedRun.rollout_snapshot.current_step_index + 1} / ${selectedRun.rollout_snapshot.step_count}` },
        { label: "Canary weight", value: selectedRun.rollout_snapshot?.current_step?.set_weight === null || selectedRun.rollout_snapshot?.current_step?.set_weight === undefined ? null : `${selectedRun.rollout_snapshot.current_step.set_weight}%` },
        { label: "Rollout evidence updated", value: formatDate(selectedRun.rollout_updated_at) },
        { label: "Rollout observation", value: selectedRun.rollout_snapshot?.observation_error ? `Unavailable (${selectedRun.rollout_snapshot.observation_error})` : undefined },
      ] },
      { title: "Lifecycle and outcome", fields: [
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

  function showTargets(healthyOnly = false) {
    targetsTable.resetFilters();
    if (healthyOnly) { targetsTable.setFilterValue("sync", "Synced"); targetsTable.setFilterValue("health", "Healthy"); }
    document.getElementById("gitops-targets")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function showFailedRuns() {
    runsTable.resetFilters(); runsTable.setFilterValue("outcome", "needs_attention");
    document.getElementById("gitops-runs")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function resetTargetForm() {
    setServiceId(""); setEnvironmentId(selectedEnvironmentId); setApplicationName(""); setProvider("argocd"); setReconciliationNamespace(""); setCluster(""); setProject("default"); setError("");
  }

  useEffect(() => {
    if (!workspaceId || !targets.data?.length) { setProviderStatuses({}); return; }
    let active = true;
    const controller = new AbortController();
    const refreshStatuses = async () => {
      const pairs = await Promise.all(targets.data!.filter((target) => target.enabled).map(async (target) => {
        try {
          const status = await apiGet<GitOpsTargetStatus>(`/workspaces/${workspaceId}/gitops-targets/${target.id}/status`, controller.signal);
          return [target.id, status] as const;
        } catch (caught) {
          if (caught instanceof DOMException && caught.name === "AbortError") return null;
          return [target.id, { application_name: target.application_name, provider: target.provider, provider_status: "unavailable", failure_code: "status_unavailable", project: null, sync_status: null, health_status: null, operation_phase: null, revision: null, destination_name: null, destination_server: null, namespace: target.reconciliation_namespace, run_status: null }] as const;
        }
      }));
      if (active) setProviderStatuses(Object.fromEntries(pairs.filter((pair): pair is readonly [string, GitOpsTargetStatus] => pair !== null)));
    };
    void refreshStatuses();
    const timer = window.setInterval(() => void refreshStatuses(), 20_000);
    return () => { active = false; controller.abort(); window.clearInterval(timer); };
  }, [targets.data, workspaceId]);

  async function act(id: string, operation: () => Promise<unknown>, message: string) {
    if (busy) return;
    setBusy(id); setError(""); setNotice("");
    try { await operation(); setNotice(message); targets.refresh(); runs.refresh(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "GitOps action failed."); }
    finally { setBusy(""); }
  }

  async function createTarget(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || busy === "new-target") return;
    await act("new-target", async () => {
      await apiPost(`/workspaces/${workspaceId}/gitops-targets`, { service_id: serviceId, environment_id: environmentId, application_name: applicationName.trim(), provider, expected_project: project.trim(), expected_cluster_name: provider === "argocd" ? cluster.trim() || undefined : undefined, reconciliation_namespace: provider === "flux" ? reconciliationNamespace.trim() : undefined });
      setShowCreate(false); resetTargetForm();
    }, `${provider === "flux" ? "Flux Kustomization" : "Argo CD application"} target registered.`);
  }

  const failedRuns = displayRuns.filter((run) => ["failed", "blocked"].includes(run.status)).length;
  const healthyTargets = displayTargets.filter((target) => providerStatuses[target.id]?.health_status === "Healthy" && providerStatuses[target.id]?.sync_status === "Synced").length;

  return <WorkspacePage eyebrow="WORKSPACE" title="GitOps" description="Inspect Argo CD and Flux reconciliation, application health and durable deployment runs." actions={<button className="catalog-primary-button" onClick={() => { resetTargetForm(); setShowCreate(true); }} disabled={!workspaceId || !canOperate || Boolean(busy)}><Plus size={14} /> Register target</button>}>
    <ResourceErrors resources={[{ label: "GitOps targets", error: targets.error }, { label: "GitOps runs", error: runs.error }, { label: "Service catalog", error: services.error }, { label: "Environments", error: environments.error }]} />
    <div className="metric-grid metric-grid-3"><Metric label="Application targets" value={displayTargets.length} icon={<GitBranch size={16} />} tone="cyan" onClick={() => showTargets()} active={!targetsTable.hasActiveFilters} /><Metric label="Healthy & synced" value={healthyTargets} icon={<GitCommitHorizontal size={16} />} tone="green" onClick={() => showTargets(true)} active={targetsTable.filters.find((filter) => filter.id === "sync")?.value === "Synced" && targetsTable.filters.find((filter) => filter.id === "health")?.value === "Healthy"} /><Metric label="Failed / blocked runs" value={failedRuns} icon={<X size={16} />} tone="red" onClick={showFailedRuns} active={runsTable.filters.find((filter) => filter.id === "outcome")?.value === "needs_attention"} /></div>
    {(notice || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || notice}<button className="notice-dismiss" onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss message"><X size={14} /></button></div>}
    {showCreate && <FormDrawer title={`Register ${provider === "flux" ? "a Flux" : "an Argo CD"} target`} description="Bind a catalog service and environment to an Argo CD Application or Flux Kustomization." dirty={Boolean(serviceId || environmentId !== selectedEnvironmentId || applicationName || reconciliationNamespace || cluster || project !== "default" || provider !== "argocd")} onClose={() => { resetTargetForm(); setShowCreate(false); }} onReset={resetTargetForm} busy={busy === "new-target"} error={error} actions={canOperate && <button form="create-gitops-target-form" type="submit" className="settings-primary-button" disabled={busy === "new-target" || !serviceId || !environmentId}><Plus size={14} />{busy === "new-target" ? "Registering…" : "Register target"}</button>}>{!canOperate ? <p className="permission-note">Platform engineer or workspace administrator access is required to manage GitOps targets.</p> : <form id="create-gitops-target-form" className="resource-form" onSubmit={(event) => void createTarget(event)}>
      <label><FieldLabel required>Service</FieldLabel><select value={serviceId} onChange={(event) => setServiceId(event.target.value)} required><option value="">Select service</option>{services.data?.items.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select></label>
      <label><FieldLabel required>Environment</FieldLabel><select value={environmentId} onChange={(event) => setEnvironmentId(event.target.value)} required><option value="">Select environment</option>{environments.data?.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)}</select></label>
      <label><FieldLabel required>Provider</FieldLabel><select value={provider} onChange={(event) => setProvider(event.target.value as "argocd" | "flux")}><option value="argocd">Argo CD</option><option value="flux">Flux</option></select></label>
      <label><FieldLabel required>{provider === "flux" ? "Kustomization name" : "Application name"}</FieldLabel><input value={applicationName} onChange={(event) => setApplicationName(event.target.value)} placeholder="checkout-production" pattern="[a-z0-9]([a-z0-9.-]*[a-z0-9])?" title="Use lowercase letters, numbers, dots and hyphens." maxLength={provider === "flux" ? 63 : 253} required /></label>
      {provider === "argocd" ? <>
        <label><FieldLabel required>Argo CD project</FieldLabel><input value={project} onChange={(event) => setProject(event.target.value)} required maxLength={80} /></label>
        <label><FieldLabel optional>Expected cluster</FieldLabel><input value={cluster} onChange={(event) => setCluster(event.target.value)} placeholder="production-us-east-1" maxLength={180} /></label>
      </> : <>
        <label><FieldLabel required>Kustomization namespace</FieldLabel><input value={reconciliationNamespace} onChange={(event) => setReconciliationNamespace(event.target.value)} placeholder="flux-system" pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" maxLength={63} required /></label>
        <p className="permission-note">Flux requires a cluster and workload namespace. Use a dedicated GitRepository; DeployForge pins it to each approved commit. Verified image checks need the target label on Pod templates.</p>
        {formEnvironment && <p className="permission-note">Environment target: {formEnvironment.cluster_name || "cluster not set"} / {formEnvironment.namespace || "namespace not set"}</p>}
      </>}
    </form>}</FormDrawer>}
    <section id="gitops-targets" className="resource-panel"><div className="resource-panel-heading"><div><h2>Application targets</h2><p>Provider status refreshes every 20 seconds; credentials remain server-side.</p></div><span className="live-indicator"><span className="status-dot" /> Argo CD + Flux status</span></div>
      <LoadingOrEmpty loading={targets.loading} empty={Boolean(targets.data && targets.data.length === 0)} label="No GitOps targets registered" />
      {selectedEnvironmentId && targets.data?.length && displayTargets.length === 0 ? <div className="resource-empty compact">No GitOps targets are bound to the selected environment.</div> : null}
      {displayTargets.length > 0 && <TableToolbar query={targetsTable.query} onQueryChange={targetsTable.setQuery} filteredCount={targetsTable.filteredItems.length} totalCount={targetsTable.totalItems} pageSize={targetsTable.pageSize} onPageSizeChange={targetsTable.setPageSize} filters={targetsTable.filters} onFilterChange={targetsTable.setFilterValue} onResetFilters={targetsTable.resetFilters} isFiltering={targetsTable.isFiltering} label="GitOps targets" />}
      {displayTargets.length > 0 && targetsTable.filteredItems.length === 0 && <div className="resource-empty compact">No GitOps targets match this filter.</div>}
      {targetsTable.pageItems.length > 0 && <div className="resource-table-wrap"><table className="resource-table has-actions"><caption className="sr-only">GitOps application targets with service scope, synchronization, health and actions</caption><thead><tr><th scope="col">Application</th><th scope="col">Service / environment</th><th scope="col">Sync</th><th scope="col">Health</th><th scope="col">Revision / destination</th><th scope="col">Target</th><th scope="col">Actions</th></tr></thead><tbody>{targetsTable.pageItems.map((target) => {
        const status = providerStatuses[target.id];
        const syncState = target.enabled
          ? status?.sync_status || status?.run_status || status?.provider_status || "checking"
          : "disabled";
        const healthState = target.enabled
          ? status?.health_status || status?.run_status || (status ? status.provider_status : "checking")
          : "disabled";
        return <tr key={target.id}>
          <td><span className="resource-cell-strong">{target.application_name}</span><small className="resource-subtext">{target.provider === "flux" ? `Flux · ${target.reconciliation_namespace || "namespace missing"}` : `Argo CD · Project ${target.expected_project}`}</small></td>
          <td><span className="resource-cell-strong">{serviceMap.get(target.service_id)?.name || "Service"}</span><small className="resource-subtext">{environmentMap.get(target.environment_id)?.name || "Environment"}</small></td>
          <td><ResourceStatus value={syncState} /></td>
          <td><ResourceStatus value={healthState} /></td>
          <td><span className="resource-mono">{status?.revision?.slice(0, 12) || "—"}</span><small className="resource-subtext">{status?.destination_name || status?.destination_server || target.expected_cluster_name || "Destination pending"}</small></td>
          <td><label className="toggle-inline"><input type="checkbox" checked={target.enabled} disabled={!canOperate || Boolean(busy)} onChange={(event) => void act(target.id, () => apiPatch(`/workspaces/${workspaceId}/gitops-targets/${target.id}`, { enabled: event.target.checked }), event.target.checked ? "GitOps target enabled." : "GitOps target disabled.")} /><span>{target.enabled ? "Enabled" : "Disabled"}</span></label></td>
          <td><div className="resource-row-actions"><ViewAction label={`GitOps target ${target.application_name}`} onClick={() => setSelected({ kind: "target", id: target.id })} /><button className="catalog-secondary-button" onClick={() => targets.refresh()} title="Refresh GitOps status"><GitCommitHorizontal size={13} /> Status</button></div></td>
        </tr>;
      })}</tbody></table><TablePagination firstItem={targetsTable.firstItem} lastItem={targetsTable.lastItem} totalItems={targetsTable.filteredItems.length} page={targetsTable.page} pageCount={targetsTable.pageCount} onPageChange={targetsTable.setPage} label="GitOps targets" /></div>}
    </section>
    <section id="gitops-runs" className="resource-panel"><div className="resource-panel-heading"><div><h2>Recent sync and rollback runs</h2><p>Release history connected to durable deployment and build records</p></div><button className="catalog-secondary-button" onClick={() => runs.refresh()}>Refresh runs</button></div>
      <LoadingOrEmpty loading={runs.loading} empty={Boolean(runs.data && runs.data.length === 0)} label="No GitOps runs recorded" />
      {selectedEnvironmentId && runs.data?.length && displayRuns.length === 0 ? <div className="resource-empty compact">No GitOps runs are linked to the selected environment.</div> : null}
      {displayRuns.length > 0 && <TableToolbar query={runsTable.query} onQueryChange={runsTable.setQuery} filteredCount={runsTable.filteredItems.length} totalCount={runsTable.totalItems} pageSize={runsTable.pageSize} onPageSizeChange={runsTable.setPageSize} filters={runsTable.filters} onFilterChange={runsTable.setFilterValue} onResetFilters={runsTable.resetFilters} isFiltering={runsTable.isFiltering} label="GitOps runs" />}
      {displayRuns.length > 0 && runsTable.filteredItems.length === 0 && <div className="resource-empty compact">No GitOps runs match this filter.</div>}
      {runsTable.pageItems.length > 0 && <div className="resource-table-wrap"><table className="resource-table has-actions"><caption className="sr-only">GitOps synchronization and rollback runs with revisions, states and actions</caption><thead><tr><th scope="col">Application</th><th scope="col">Action</th><th scope="col">Desired revision</th><th scope="col">State</th><th scope="col">Started</th><th scope="col">Actions</th></tr></thead><tbody>{runsTable.pageItems.map((run) => <tr key={run.id}><td><span className="resource-cell-strong">{run.application_name}</span><small className="resource-subtext">Deployment {run.deployment_id.slice(0, 8)}</small></td><td>{run.action_kind.replaceAll("_", " ")}</td><td><span className="resource-mono">{run.desired_revision?.slice(0, 12) || "—"}</span><small className="resource-subtext">{run.artifact_digest?.slice(0, 20) || ""}</small></td><td><ResourceStatus value={run.status} />{run.failure_message && <small className="resource-subtext resource-failure">{run.failure_message}</small>}</td><td>{formatDate(run.started_at || run.created_at)}</td><td><div className="resource-row-actions"><ViewAction label={`GitOps run ${run.application_name}`} onClick={() => setSelected({ kind: "run", id: run.id })} />{run.status === "blocked" && canOperate ? <button className="row-action" disabled={Boolean(busy)} onClick={() => void act(run.id, () => apiPost(`/workspaces/${workspaceId}/gitops-runs/${run.id}/retry`), "GitOps retry queued.")} title="Retry blocked run"><RotateCcw size={14} /></button> : null}</div></td></tr>)}</tbody></table><TablePagination firstItem={runsTable.firstItem} lastItem={runsTable.lastItem} totalItems={runsTable.filteredItems.length} page={runsTable.page} pageCount={runsTable.pageCount} onPageChange={runsTable.setPage} label="GitOps runs" /></div>}
    </section>
    {record && <RecordDrawer record={record} onClose={() => setSelected(null)}>
      {selectedRun && runDetail && <GitOpsRunDrawerContent
        workspaceId={workspaceId}
        run={runDetail}
        canOperate={canOperate}
        onUpdated={(detail) => setRunDetail(detail)}
        onRefresh={() => runs.refresh()}
      />}
    </RecordDrawer>}
  </WorkspacePage>;
}

function Metric({ label, value, icon, tone, onClick, active = false }: { label: string; value: number; icon: React.ReactNode; tone: string; onClick: () => void; active?: boolean }) { return <button type="button" className={`metric-card metric-filter-card ${tone}${active ? " is-active" : ""}`} onClick={onClick} aria-pressed={active} title={`Show ${label.toLowerCase()}`}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span></button>; }

function GitOpsRunDrawerContent({
  workspaceId,
  run,
  canOperate,
  onUpdated,
  onRefresh,
}: {
  workspaceId: string | null;
  run: GitOpsRunDetail;
  canOperate: boolean;
  onUpdated: (detail: GitOpsRunDetail) => void;
  onRefresh: () => void;
}) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const snapshot = run.rollout_snapshot;
  const active = run.action_kind === "deploy" && ["syncing", "verifying"].includes(run.status);
  const inFlight = run.rollout_actions.some((action) => ["queued", "dispatching"].includes(action.status));
  const canPromote = canOperate && active && snapshot?.paused === true && snapshot.current_step_index !== null && !snapshot.observation_error && !inFlight;
  const canAbort = canOperate && active && ["paused", "progressing"].includes((snapshot?.phase || "").toLowerCase()) && !snapshot?.observation_error && !inFlight;

  async function submit(action: "promote" | "abort") {
    if (!workspaceId || comment.trim().length < 3 || busy) return;
    if (action === "abort" && !window.confirm("Abort this active rollout stage? Argo Rollouts will stop its current progression.")) return;
    setBusy(action);
    setError("");
    setNotice("");
    try {
      const queued = await apiPost<GitOpsRolloutAction>(
        `/workspaces/${workspaceId}/gitops-runs/${run.id}/${action}`,
        { comment: comment.trim() },
      );
      setNotice(`${action === "promote" ? "Stage promotion" : "Rollout abort"} queued (${queued.status}).`);
      setComment("");
      const detail = await apiGet<GitOpsRunDetail>(`/workspaces/${workspaceId}/gitops-runs/${run.id}`);
      onUpdated(detail);
      onRefresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not queue the rollout action.");
    } finally {
      setBusy("");
    }
  }

  return <>
    <section className="record-detail-section">
      <h3>Progressive rollout</h3>
      {!snapshot ? <p className="record-detail-note">Argo Rollouts evidence has not been observed for this run.</p> : <>
        <div className="record-state-summary"><ResourceStatus value={snapshot.phase || "unknown"} /><span>{snapshot.paused ? "Waiting at a manual stage gate" : `Stage ${snapshot.current_step_index === null ? "—" : snapshot.current_step_index + 1} of ${snapshot.step_count}`}</span></div>
        <p className="record-detail-note">Namespace {snapshot.namespace || "—"} · Weight {snapshot.current_step.set_weight === null ? "—" : `${snapshot.current_step.set_weight}%`} · Updated {formatDate(run.rollout_updated_at)}</p>
        {snapshot.observation_error && <p className="resource-failure">Latest Rollouts observation failed: {snapshot.observation_error}</p>}
        {snapshot.message && <p className="record-detail-note">{snapshot.message}</p>}
        <div className="rollout-replica-summary">{Object.entries(snapshot.replicas).map(([name, value]) => <span key={name}><small>{name}</small><strong>{value ?? "—"}</strong></span>)}</div>
        <div className="rollout-analysis-list">
          {snapshot.analysis_runs.map((analysis, index) => <article className="rollout-analysis-card" key={`${analysis.name || "analysis"}-${index}`}>
            <div className="resource-panel-heading"><strong>{analysis.name || `Analysis ${index + 1}`}</strong><ResourceStatus value={analysis.phase || "unknown"} /></div>
            {analysis.message && <p>{analysis.message}</p>}
            {analysis.metrics.length > 0 && <div className="resource-table-wrap"><table className="resource-table"><caption className="sr-only">Analysis metrics for {analysis.name || `Analysis ${index + 1}`}</caption><thead><tr><th>Metric</th><th>Phase</th><th>Count</th><th>Latest value</th><th>Message</th></tr></thead><tbody>{analysis.metrics.map((metric, metricIndex) => {
              const latest = metric.measurements[metric.measurements.length - 1];
              return <tr key={`${metric.name || "metric"}-${metricIndex}`}><td>{metric.name || "—"}</td><td><ResourceStatus value={metric.phase || "unknown"} /></td><td>{metric.count ?? latest?.count ?? "—"}</td><td>{latest?.value || "—"}</td><td>{latest?.message || "—"}</td></tr>;
            })}</tbody></table></div>}
          </article>)}
          {!snapshot.analysis_runs.length && <p className="record-detail-note">No AnalysisRun results are attached to this rollout snapshot.</p>}
        </div>
      </>}
    </section>
    {run.rollout_actions.length > 0 && <section className="record-detail-section"><h3>Stage action history</h3><div className="resource-table-wrap"><table className="resource-table"><caption className="sr-only">Stage promotion and abort history</caption><thead><tr><th>Action</th><th>Stage</th><th>Status</th><th>Requested</th><th>Reason / result</th></tr></thead><tbody>{run.rollout_actions.map((action) => <tr key={action.id}><td>{action.action}</td><td>{action.requested_step === null ? "—" : Number(action.requested_step) + 1}</td><td><ResourceStatus value={action.status} /></td><td>{formatDate(action.created_at)}</td><td>{action.failure_message || action.comment}</td></tr>)}</tbody></table></div></section>}
    {run.events.length > 0 && <section className="record-detail-section"><h3>Run timeline</h3><ol className="gitops-event-list">{run.events.slice().reverse().map((event) => <li key={event.id}><div><strong>{event.message}</strong><small>{formatDate(event.created_at)} · {event.actor_type}</small></div><code>{event.event_type}</code></li>)}</ol></section>}
    {active && <section className="record-detail-section"><h3>Operator controls</h3>
      {!canOperate && <p className="permission-note">Platform engineer or workspace administrator access is required to control rollout stages.</p>}
      {canOperate && !snapshot && <p className="record-detail-note">Stage controls appear after the worker records a Rollout status.</p>}
      {canOperate && snapshot && <>
        <label className="rollout-comment-label">Reason for this action<textarea value={comment} onChange={(event) => setComment(event.target.value)} maxLength={1000} minLength={3} placeholder="Describe why this stage should proceed or stop." /></label>
        {(error || notice) && <p className={error ? "resource-failure" : "record-detail-note"} role={error ? "alert" : "status"}>{error || notice}</p>}
        <div className="rollout-control-actions">
          <button className="settings-primary-button" disabled={!canPromote || comment.trim().length < 3 || Boolean(busy)} onClick={() => void submit("promote")}>{busy === "promote" ? "Queuing…" : "Promote current stage"}</button>
          <button className="catalog-secondary-button rollout-abort-button" disabled={!canAbort || comment.trim().length < 3 || Boolean(busy)} onClick={() => void submit("abort")}>{busy === "abort" ? "Queuing…" : "Abort rollout"}</button>
        </div>
        {inFlight && <p className="record-detail-note">A stage action is already queued or dispatching.</p>}
      </>}
    </section>}
  </>;
}
