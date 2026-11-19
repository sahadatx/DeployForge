import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ArrowDownToLine, Check, CircleX, Clock3, GitCommitHorizontal, Rocket, RotateCcw, ShieldCheck, X } from "lucide-react";
import { apiGet, apiPost, type BuildRun, type Deployment, type DeploymentEvent, type Environment, type PromotableArtifact, type ServiceCatalog } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { FieldLabel, FormDrawer } from "../components/FormDrawer";
import { RecordDrawer, ViewAction } from "../components/RecordDrawer";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, TablePagination, TableToolbar, useTableView, useWorkspaceResource, WorkspacePage } from "../components/WorkspacePage";

type Tab = "releases" | "builds";
const APPROVAL_STATES = new Set(["awaiting_approval", "awaiting_release_approval"]);
const ACTIVE_STATES = new Set(["queued", "building", "deploying", "verifying", "validating"]);
const buildProviderName = (provider: string) => ({
  azure_devops: "Azure DevOps",
  github_actions: "GitHub Actions",
  gitlab_ci: "GitLab CI",
  artifact_promotion: "Artifact promotion",
}[provider] || provider.replaceAll("_", " "));
const buildWorkflowName = (build: BuildRun) => {
  const pipelineMatch = build.workflow_path.match(/^azure-pipeline:(\d+):/);
  return build.provider === "azure_devops"
    ? pipelineMatch ? `Pipeline ${pipelineMatch[1]}` : "Pipeline not mapped"
    : build.workflow_path;
};

export default function DeploymentsPage() {
  const { workspaceId, environmentId, canOperate, session } = usePlatformContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedReleaseId = searchParams.get("release");
  const deployments = useWorkspaceResource<Deployment[]>(workspaceId ? `/workspaces/${workspaceId}/deployments?limit=100${environmentId ? `&environment_id=${environmentId}` : ""}` : null, 20_000, "array");
  const builds = useWorkspaceResource<BuildRun[]>(workspaceId ? `/workspaces/${workspaceId}/build-runs?limit=100` : null, 20_000, "array");
  const services = useWorkspaceResource<ServiceCatalog>(workspaceId ? `/workspaces/${workspaceId}/services?limit=100` : null, 60_000, "catalog");
  const environments = useWorkspaceResource<Environment[]>(workspaceId ? `/workspaces/${workspaceId}/environments` : null, 60_000);
  const [tab, setTab] = useState<Tab>("releases");
  const [showRequest, setShowRequest] = useState(false);
  const [serviceId, setServiceId] = useState("");
  const [targetEnvironment, setTargetEnvironment] = useState("");
  const [version, setVersion] = useState("");
  const [sourceRef, setSourceRef] = useState("");
  const [releaseMode, setReleaseMode] = useState<"build" | "promote">("build");
  const [promotionArtifactId, setPromotionArtifactId] = useState("");
  const [promotionRevision, setPromotionRevision] = useState("");
  const [selected, setSelected] = useState<Deployment | null>(null);
  const [events, setEvents] = useState<DeploymentEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState("");
  const [selectedBuild, setSelectedBuild] = useState<BuildRun | null>(null);
  const historyRequest = useRef<AbortController | null>(null);
  const [busyId, setBusyId] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const promotionArtifactPath = workspaceId && showRequest && releaseMode === "promote" && serviceId && targetEnvironment
    ? `/workspaces/${workspaceId}/promotion-artifacts?service_id=${encodeURIComponent(serviceId)}&environment_id=${encodeURIComponent(targetEnvironment)}`
    : null;
  const promotionArtifacts = useWorkspaceResource<PromotableArtifact[]>(promotionArtifactPath, 0, "array");

  const serviceMap = useMemo(() => new Map((services.data?.items || []).map((service) => [service.id, service])), [services.data]);
  const environmentMap = useMemo(() => new Map((environments.data || []).map((environment) => [environment.id, environment])), [environments.data]);
  const deploymentsTable = useTableView(deployments.data || [], (item) => `${serviceMap.get(item.service_id)?.name || ""} ${environmentMap.get(item.environment_id)?.name || ""} ${item.version} ${item.source_ref || ""} ${item.status} ${item.failure_message || ""}`, { resetKey: `${workspaceId}:${environmentId}`, filters: [{ id: "status", label: "Status", getValue: (item) => APPROVAL_STATES.has(item.status) ? "awaiting_review" : ACTIVE_STATES.has(item.status) ? "in_progress" : item.status }, { id: "service", label: "Service", getValue: (item) => serviceMap.get(item.service_id)?.name || item.service_id }] });
  const buildsTable = useTableView(builds.data || [], (item) => `${serviceMap.get(item.service_id)?.name || ""} ${buildProviderName(item.provider)} ${item.source_ref} ${item.commit_sha || ""} ${item.status} ${item.workflow_path} ${item.failure_message || ""}`, { resetKey: workspaceId, filters: [{ id: "status", label: "Status", getValue: (item) => item.status }, { id: "provider", label: "Provider", getValue: (item) => buildProviderName(item.provider) }] });

  useEffect(() => {
    setSelected(null);
    setSelectedBuild(null);
    historyRequest.current?.abort();
    return () => historyRequest.current?.abort();
  }, [workspaceId, environmentId]);

  useEffect(() => { setShowRequest(false); resetReleaseForm(); }, [workspaceId]);

  function resetReleaseForm() {
    setServiceId(""); setTargetEnvironment(""); setVersion(""); setSourceRef("");
    setReleaseMode("build"); setPromotionArtifactId(""); setPromotionRevision(""); setError("");
  }

  async function refresh() {
    deployments.refresh();
    builds.refresh();
  }

  async function runAction(id: string, action: () => Promise<unknown>, message: string) {
    if (busyId) return;
    setBusyId(id);
    setNotice("");
    setError("");
    try {
      await action();
      setNotice(message);
      await refresh();
      if (selected?.id === id) await openDetails(selected);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The requested release action failed.");
    } finally {
      setBusyId("");
    }
  }

  async function requestRelease(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || !serviceId || !targetEnvironment || busyId === "new-release") return;
    if (releaseMode === "promote" && (!promotionArtifactId || !/^[0-9a-f]{40,64}$/.test(promotionRevision.trim()))) return;
    await runAction("new-release", async () => {
      await apiPost(`/workspaces/${workspaceId}/deployments`, {
        service_id: serviceId,
        environment_id: targetEnvironment,
        version: version.trim(),
        ...(releaseMode === "promote"
          ? { artifact_id: promotionArtifactId, gitops_revision: promotionRevision.trim() }
          : { source_ref: sourceRef.trim() || undefined }),
      });
      resetReleaseForm();
      setShowRequest(false);
    }, "Release request added to the durable queue.");
  }

  async function openDetails(deployment: Deployment) {
    historyRequest.current?.abort();
    const controller = new AbortController();
    historyRequest.current = controller;
    setSelected(deployment);
    setEvents([]);
    setEventsError("");
    setEventsLoading(true);
    try {
      const history = await apiGet<DeploymentEvent[]>(`/workspaces/${workspaceId}/deployments/${deployment.id}/events`, controller.signal);
      if (!controller.signal.aborted) setEvents(history);
    } catch (caught) {
      if (!controller.signal.aborted) setEventsError(caught instanceof Error ? caught.message : "Release history could not be loaded.");
    } finally {
      if (!controller.signal.aborted) setEventsLoading(false);
    }
  }

  useEffect(() => {
    if (!requestedReleaseId || !workspaceId || deployments.data === null) return;
    const loadedDeployments = deployments.data;
    let active = true;
    const controller = new AbortController();
    void (async () => {
      try {
        const deployment = loadedDeployments.find((item) => item.id === requestedReleaseId)
          || await apiGet<Deployment>(`/workspaces/${workspaceId}/deployments/${encodeURIComponent(requestedReleaseId)}`, controller.signal);
        if (active) await openDetails(deployment);
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : "The release selected from search could not be opened.");
      } finally {
        if (active) {
          const next = new URLSearchParams(searchParams);
          next.delete("release");
          setSearchParams(next, { replace: true });
        }
      }
    })();
    return () => { active = false; controller.abort(); };
  }, [requestedReleaseId, workspaceId, deployments.data, searchParams, setSearchParams]);

  const pendingApprovals = (deployments.data || []).filter((item) => APPROVAL_STATES.has(item.status)).length;
  const activeReleases = (deployments.data || []).filter((item) => ["queued", "building", "deploying", "verifying", "validating"].includes(item.status)).length;
  const failedReleases = (deployments.data || []).filter((item) => item.status === "failed").length;

  function navigateTabs(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? "releases" : event.key === "End" ? "builds" : tab === "releases" ? "builds" : "releases";
    setTab(next);
    document.getElementById(`${next}-tab`)?.focus();
  }

  return (
    <WorkspacePage eyebrow="WORKSPACE" title="Deployments" description="Queue releases, review approval gates and follow every durable state transition." actions={<button className="catalog-primary-button" onClick={() => { resetReleaseForm(); setShowRequest(true); }} disabled={!workspaceId || !canOperate || Boolean(busyId)}><Rocket size={14} /> Request release</button>}>
      <ResourceErrors resources={[{ label: "Deployments", error: deployments.error }, { label: "Build runs", error: builds.error }, { label: "Service catalog", error: services.error }, { label: "Environments", error: environments.error }]} />
      <div className="metric-grid metric-grid-3">
        <Metric label="Pending approvals" value={pendingApprovals} tone="amber" icon={<Clock3 size={16} />} active={tab === "releases" && deploymentsTable.filters[0]?.value === "awaiting_review"} onClick={() => { setTab("releases"); deploymentsTable.resetFilters(); deploymentsTable.setFilterValue("status", "awaiting_review"); }} />
        <Metric label="In progress" value={activeReleases} tone="cyan" icon={<Rocket size={16} />} active={tab === "releases" && deploymentsTable.filters[0]?.value === "in_progress"} onClick={() => { setTab("releases"); deploymentsTable.resetFilters(); deploymentsTable.setFilterValue("status", "in_progress"); }} />
        <Metric label="Failed releases" value={failedReleases} tone="red" icon={<CircleX size={16} />} active={tab === "releases" && deploymentsTable.filters[0]?.value === "failed"} onClick={() => { setTab("releases"); deploymentsTable.resetFilters(); deploymentsTable.setFilterValue("status", "failed"); }} />
      </div>

      {(notice || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || notice}<button className="notice-dismiss" onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss message"><X size={14} /></button></div>}

      {showRequest && <FormDrawer title={releaseMode === "promote" ? "Promote an artifact" : "Request a release"} description={releaseMode === "promote" ? "Reuse an artifact from a healthy deployment. The target manifest revision still passes approval, policy and GitOps verification." : "A durable deployment request will be validated and sent through build, policy and approval gates."} dirty={Boolean(serviceId || targetEnvironment || version || sourceRef || promotionArtifactId || promotionRevision || releaseMode !== "build")} onClose={() => { resetReleaseForm(); setShowRequest(false); }} onReset={resetReleaseForm} busy={busyId === "new-release"} error={error} actions={canOperate && <button form="request-release-form" type="submit" className="settings-primary-button" disabled={busyId === "new-release"}><Rocket size={14} />{busyId === "new-release" ? "Queueing…" : releaseMode === "promote" ? "Queue promotion" : "Queue release"}</button>}>
        {!canOperate ? <p className="permission-note">Platform engineer or workspace administrator access is required to request releases.</p> : <form id="request-release-form" className="resource-form" onSubmit={(event) => void requestRelease(event)}>
          <label><FieldLabel required>Release method</FieldLabel><select value={releaseMode} onChange={(event) => { setReleaseMode(event.target.value as "build" | "promote"); setPromotionArtifactId(""); }}><option value="build">Build a new artifact</option><option value="promote">Promote a deployed artifact</option></select></label>
          <label><FieldLabel required>Service</FieldLabel><select data-initial-focus required value={serviceId} onChange={(event) => { setServiceId(event.target.value); setPromotionArtifactId(""); }}><option value="">Select a service</option>{(services.data?.items || []).map((service) => <option key={service.id} value={service.id}>{service.name} · {service.slug}</option>)}</select></label>
          <label><FieldLabel required>Environment</FieldLabel><select required value={targetEnvironment} onChange={(event) => { setTargetEnvironment(event.target.value); setPromotionArtifactId(""); }}><option value="">Select an environment</option>{environments.data?.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)}</select></label>
          <label><FieldLabel required>Target release version</FieldLabel><input required value={version} onChange={(event) => setVersion(event.target.value)} placeholder="v2.8.1" maxLength={160} /></label>
          {releaseMode === "build" ? <label><FieldLabel optional>Source ref</FieldLabel><input value={sourceRef} onChange={(event) => setSourceRef(event.target.value)} placeholder="main or commit SHA" maxLength={255} /></label> : <>
            <label><FieldLabel required>Healthy source artifact</FieldLabel><select required value={promotionArtifactId} onChange={(event) => setPromotionArtifactId(event.target.value)} disabled={!serviceId || !targetEnvironment || promotionArtifacts.loading}><option value="">{!serviceId || !targetEnvironment ? "Select service and target first" : promotionArtifacts.loading ? "Loading eligible artifacts…" : "Select an artifact"}</option>{promotionArtifacts.data?.map((artifact) => <option key={artifact.id} value={artifact.id}>{artifact.name} · {artifact.source_version} · {environmentMap.get(artifact.source_environment_id)?.name || "source environment"} · {artifact.digest.slice(0, 19)}…</option>)}</select>{promotionArtifacts.error && <small className="resource-failure">{promotionArtifacts.error}</small>}{promotionArtifacts.data?.length === 0 && !promotionArtifacts.loading && <small className="resource-subtext">No successfully deployed artifacts are eligible for this service and target.</small>}</label>
            <label><FieldLabel required>Target GitOps manifest revision</FieldLabel><input required value={promotionRevision} onChange={(event) => setPromotionRevision(event.target.value)} placeholder="40–64 character commit SHA" minLength={40} maxLength={64} pattern="[0-9a-f]{40,64}" autoComplete="off" spellCheck={false} /><small className="resource-subtext">The exact target commit must sync healthy with the promoted image digest.</small></label>
          </>}
        </form>}
      </FormDrawer>}

      <section className="resource-panel">
        <div className="resource-panel-heading"><div><div className="catalog-tabs" role="tablist"><button className={tab === "releases" ? "is-active" : ""} id="releases-tab" aria-controls="releases-panel" tabIndex={tab === "releases" ? 0 : -1} onKeyDown={navigateTabs} onClick={() => setTab("releases")} role="tab" aria-selected={tab === "releases"}>Release requests <span>{deployments.data?.length || 0}</span></button><button className={tab === "builds" ? "is-active" : ""} id="builds-tab" aria-controls="builds-panel" tabIndex={tab === "builds" ? 0 : -1} onKeyDown={navigateTabs} onClick={() => setTab("builds")} role="tab" aria-selected={tab === "builds"}>Build runs <span>{builds.data?.length || 0}</span></button></div><p>Live operational state from the release control plane</p></div><button className="catalog-secondary-button" onClick={() => void refresh()}><ArrowDownToLine size={13} /> Refresh</button></div>
        {tab === "releases" ? <div role="tabpanel" id="releases-panel" aria-labelledby="releases-tab">
          <LoadingOrEmpty loading={deployments.loading} empty={Boolean(deployments.data && deployments.data.length === 0)} label="No release requests in this workspace" />
          {deployments.data && deployments.data.length > 0 && <TableToolbar query={deploymentsTable.query} onQueryChange={deploymentsTable.setQuery} filteredCount={deploymentsTable.filteredItems.length} totalCount={deploymentsTable.totalItems} pageSize={deploymentsTable.pageSize} onPageSizeChange={deploymentsTable.setPageSize} label="releases" filters={deploymentsTable.filters} onFilterChange={deploymentsTable.setFilterValue} onResetFilters={deploymentsTable.resetFilters} isFiltering={deploymentsTable.isFiltering} />}
          {deployments.data && deployments.data.length > 0 && deploymentsTable.filteredItems.length === 0 && <div className="resource-empty compact">No releases match this filter.</div>}
          {deploymentsTable.pageItems.length > 0 && <div className="resource-table-wrap"><table className="resource-table has-actions"><caption className="sr-only">Release requests and approval actions</caption><thead><tr><th>Release</th><th>Target</th><th>State</th><th>Requested</th><th>Attempts</th><th>Actions</th></tr></thead><tbody>{deploymentsTable.pageItems.map((deployment) => {
            const service = serviceMap.get(deployment.service_id);
            const env = environmentMap.get(deployment.environment_id);
            const pending = APPROVAL_STATES.has(deployment.status);
            const requestedByCurrentUser = deployment.requested_by === session.user.id;
            return <tr key={deployment.id}>
              <td><button className="resource-primary-cell" onClick={() => void openDetails(deployment)}><strong>{service?.name || `Service ${deployment.service_id.slice(0, 8)}`}</strong><small>{deployment.version}{deployment.source_ref ? ` · ${deployment.source_ref}` : ""}</small></button></td>
              <td><span className="resource-cell-strong">{env?.name || "Environment"}</span><small className="resource-subtext">{env?.environment_type || "—"}</small></td>
              <td><ResourceStatus value={deployment.status} />{deployment.failure_message && <small className="resource-subtext resource-failure">{deployment.failure_message}</small>}</td>
              <td>{formatDate(deployment.created_at)}</td><td>{deployment.attempt_count}/{deployment.max_attempts}</td>
              <td><div className="resource-row-actions">
                <ViewAction onClick={() => void openDetails(deployment)} label={`release ${deployment.version}`} />
                {pending && canOperate && !requestedByCurrentUser && <><button className="row-action approve" disabled={Boolean(busyId)} onClick={() => void runAction(deployment.id, () => apiPost(`/workspaces/${workspaceId}/deployments/${deployment.id}/approval`, { decision: "approve", comment: "Approved in DeployForge." }), "Release approved.")} title="Approve release"><Check size={14} /></button><button className="row-action reject" disabled={Boolean(busyId)} onClick={() => void runAction(deployment.id, () => apiPost(`/workspaces/${workspaceId}/deployments/${deployment.id}/approval`, { decision: "reject", comment: "Rejected in DeployForge." }), "Release rejected.")} title="Reject release"><X size={14} /></button></>}
                {pending && requestedByCurrentUser && <span className="approval-hint"><ShieldCheck size={12} /> Needs another approver</span>}
                {deployment.status === "failed" && canOperate && <button className="row-action" disabled={Boolean(busyId) || deployment.attempt_count >= deployment.max_attempts} onClick={() => void runAction(deployment.id, () => apiPost(`/workspaces/${workspaceId}/deployments/${deployment.id}/retry`), "Release retry queued.")} title="Retry release"><RotateCcw size={14} /></button>}
                {["queued", "awaiting_approval", "awaiting_release_approval"].includes(deployment.status) && canOperate && <button className="row-action reject" disabled={Boolean(busyId)} onClick={() => void runAction(deployment.id, () => apiPost(`/workspaces/${workspaceId}/deployments/${deployment.id}/cancel`), "Release canceled.")} title="Cancel release"><CircleX size={14} /></button>}
                {deployment.status === "succeeded" && canOperate && <button className="row-action" disabled={Boolean(busyId)} onClick={() => { if (window.confirm(`Queue rollback for ${service?.name || "this service"} to its previous healthy revision?`)) void runAction(deployment.id, () => apiPost(`/workspaces/${workspaceId}/deployments/${deployment.id}/rollback`), "Rollback queued."); }} title="Rollback to previous healthy revision"><RotateCcw size={14} /></button>}
                {!pending && deployment.status !== "failed" && deployment.status !== "succeeded" && !["queued", "awaiting_approval", "awaiting_release_approval"].includes(deployment.status) && <span className="muted-cell">—</span>}
              </div></td>
            </tr>;
          })}</tbody></table><TablePagination firstItem={deploymentsTable.firstItem} lastItem={deploymentsTable.lastItem} totalItems={deploymentsTable.filteredItems.length} page={deploymentsTable.page} pageCount={deploymentsTable.pageCount} onPageChange={deploymentsTable.setPage} label="releases" /></div>}
        </div> : <div role="tabpanel" id="builds-panel" aria-labelledby="builds-tab">
          <LoadingOrEmpty loading={builds.loading} empty={Boolean(builds.data && builds.data.length === 0)} label="No CI build runs recorded" />
          {builds.data && builds.data.length > 0 && <TableToolbar query={buildsTable.query} onQueryChange={buildsTable.setQuery} filteredCount={buildsTable.filteredItems.length} totalCount={buildsTable.totalItems} pageSize={buildsTable.pageSize} onPageSizeChange={buildsTable.setPageSize} label="build runs" filters={buildsTable.filters} onFilterChange={buildsTable.setFilterValue} onResetFilters={buildsTable.resetFilters} isFiltering={buildsTable.isFiltering} />}
          {builds.data && builds.data.length > 0 && buildsTable.filteredItems.length === 0 && <div className="resource-empty compact">No build runs match this filter.</div>}
          {buildsTable.pageItems.length > 0 && <div className="resource-table-wrap"><table className="resource-table has-actions"><caption className="sr-only">Continuous integration build runs</caption><thead><tr><th>Build</th><th>Provider</th><th>Source</th><th>State</th><th>Updated</th><th>Actions</th></tr></thead><tbody>{buildsTable.pageItems.map((build) => <tr key={build.id}><td><span className="resource-cell-strong">{serviceMap.get(build.service_id)?.name || "Service"}</span><small className="resource-subtext">Attempt {build.attempt_number} · {buildWorkflowName(build)}</small></td><td>{buildProviderName(build.provider)}</td><td><span className="resource-cell-strong">{build.source_ref}</span><small className="resource-subtext">Source {build.commit_sha?.slice(0, 12) || "commit pending"}</small>{build.gitops_revision && <small className="resource-subtext">GitOps {build.gitops_revision.slice(0, 12)}</small>}</td><td><ResourceStatus value={build.status} />{build.failure_message && <small className="resource-subtext resource-failure">{build.failure_message}</small>}{build.run_url && <a className="resource-inline-link" href={build.run_url} target="_blank" rel="noreferrer">External run ↗</a>}</td><td>{formatDate(build.updated_at)}</td><td><div className="resource-row-actions"><ViewAction onClick={() => setSelectedBuild(build)} label={`build ${build.source_ref}`} />{build.status === "blocked" && canOperate ? <button className="row-action" disabled={Boolean(busyId) || build.attempt_count >= build.max_attempts} onClick={() => void runAction(build.id, () => apiPost(`/workspaces/${workspaceId}/build-runs/${build.id}/retry`), "Build retry queued.")} title="Retry blocked build"><RotateCcw size={14} /></button> : <span className="muted-cell">—</span>}</div></td></tr>)}</tbody></table><TablePagination firstItem={buildsTable.firstItem} lastItem={buildsTable.lastItem} totalItems={buildsTable.filteredItems.length} page={buildsTable.page} pageCount={buildsTable.pageCount} onPageChange={buildsTable.setPage} label="build runs" /></div>}
        </div>}
      </section>

      {selected && <RecordDrawer onClose={() => { historyRequest.current?.abort(); setSelected(null); }} record={{ title: `Release ${selected.version}`, eyebrow: "RELEASE RECORD", status: selected.status, description: `${serviceMap.get(selected.service_id)?.name || "Service"} · ${environmentMap.get(selected.environment_id)?.name || "Environment"}`, sections: [{ title: "Release details", fields: [{ label: "Version", value: selected.version }, { label: "Source ref", value: selected.source_ref, mono: true }, { label: "Promoted artifact", value: selected.promoted_artifact_id, mono: true }, { label: "Target GitOps revision", value: selected.promotion_gitops_revision, mono: true }, { label: "Attempts", value: `${selected.attempt_count} of ${selected.max_attempts}` }, { label: "Requested", value: formatDate(selected.created_at) }, { label: "Last updated", value: formatDate(selected.updated_at) }, { label: "Failure", value: selected.failure_message || selected.failure_code }] }] }}>
        {selected.requested_by === session.user.id && APPROVAL_STATES.has(selected.status) && <div className="approval-notice" role="status"><ShieldCheck size={15} /><span><strong>Independent approval required</strong><small>You requested this release. A different workspace approver must review it before the workflow continues.</small></span></div>}
        <div className="drawer-section-heading"><GitCommitHorizontal size={15} /><strong>Release timeline</strong></div>
        {eventsError && <div className="settings-notice is-error" role="alert">{eventsError}</div>}
        {eventsLoading ? <div className="resource-loading"><span className="spinner" />Loading release history…</div> : events.length ? <ol className="timeline-list">{events.map((event) => <li key={event.id}><span className="timeline-marker" /><div><strong>{event.message}</strong><small>{event.event_type.replaceAll("_", " ")} · {formatDate(event.created_at)}</small></div></li>)}</ol> : !eventsError && <div className="resource-empty compact">No release events available.</div>}
      </RecordDrawer>}
      {selectedBuild && <RecordDrawer onClose={() => setSelectedBuild(null)} record={{ title: `Build · ${selectedBuild.source_ref}`, eyebrow: "BUILD RUN", status: selectedBuild.status, description: serviceMap.get(selectedBuild.service_id)?.name || "Service build", sections: [{ title: "Build evidence", fields: [{ label: "Provider", value: buildProviderName(selectedBuild.provider) }, { label: "Workflow", value: selectedBuild.workflow_path, mono: true }, { label: "Source ref", value: selectedBuild.source_ref, mono: true }, { label: "Commit", value: selectedBuild.commit_sha, mono: true }, { label: "GitOps revision", value: selectedBuild.gitops_revision, mono: true }, { label: "Attempt", value: `${selectedBuild.attempt_number} · ${selectedBuild.attempt_count}/${selectedBuild.max_attempts} retries` }, { label: "Updated", value: formatDate(selectedBuild.updated_at) }, { label: "Failure", value: selectedBuild.failure_message }, { label: "Provider run", value: selectedBuild.run_url ? <a className="resource-inline-link" href={selectedBuild.run_url} target="_blank" rel="noreferrer">Open build run ↗</a> : null }] }] }} />}
    </WorkspacePage>
  );
}

function Metric({ label, value, tone, icon, active, onClick }: { label: string; value: number; tone: string; icon: React.ReactNode; active?: boolean; onClick: () => void }) {
  return <button type="button" className={`metric-card metric-filter-card ${tone} ${active ? "is-active" : ""}`} onClick={onClick} aria-pressed={Boolean(active)}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span></button>;
}

