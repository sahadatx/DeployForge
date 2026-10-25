import { useMemo, useState } from "react";
import { ArrowDownToLine, Check, CircleX, Clock3, GitCommitHorizontal, Rocket, RotateCcw, X } from "lucide-react";
import { apiGet, apiPost, type BuildRun, type Deployment, type DeploymentEvent, type Environment, type ServiceCatalog } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, useWorkspaceResource, WorkspacePage } from "../components/WorkspacePage";

type Tab = "releases" | "builds";

export default function DeploymentsPage() {
  const { workspaceId, environmentId, canOperate } = usePlatformContext();
  const deployments = useWorkspaceResource<Deployment[]>(workspaceId ? `/workspaces/${workspaceId}/deployments?limit=100${environmentId ? `&environment_id=${environmentId}` : ""}` : null);
  const builds = useWorkspaceResource<BuildRun[]>(workspaceId ? `/workspaces/${workspaceId}/build-runs?limit=100` : null);
  const services = useWorkspaceResource<ServiceCatalog>(workspaceId ? `/workspaces/${workspaceId}/services?limit=100` : null, 60_000);
  const environments = useWorkspaceResource<Environment[]>(workspaceId ? `/workspaces/${workspaceId}/environments` : null, 60_000);
  const [tab, setTab] = useState<Tab>("releases");
  const [showRequest, setShowRequest] = useState(false);
  const [serviceId, setServiceId] = useState("");
  const [targetEnvironment, setTargetEnvironment] = useState("");
  const [version, setVersion] = useState("");
  const [sourceRef, setSourceRef] = useState("");
  const [selected, setSelected] = useState<Deployment | null>(null);
  const [events, setEvents] = useState<DeploymentEvent[]>([]);
  const [busyId, setBusyId] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const serviceMap = useMemo(() => new Map((services.data?.items || []).map((service) => [service.id, service])), [services.data]);
  const environmentMap = useMemo(() => new Map((environments.data || []).map((environment) => [environment.id, environment])), [environments.data]);

  async function refresh() {
    deployments.refresh();
    builds.refresh();
  }

  async function runAction(id: string, action: () => Promise<unknown>, message: string) {
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
    if (!workspaceId || !serviceId || !targetEnvironment) return;
    await runAction("new-release", async () => {
      await apiPost(`/workspaces/${workspaceId}/deployments`, {
        service_id: serviceId,
        environment_id: targetEnvironment,
        version,
        source_ref: sourceRef || undefined,
      });
      setVersion("");
      setSourceRef("");
      setShowRequest(false);
    }, "Release request added to the durable queue.");
  }

  async function openDetails(deployment: Deployment) {
    setSelected(deployment);
    try {
      setEvents(await apiGet<DeploymentEvent[]>(`/workspaces/${workspaceId}/deployments/${deployment.id}/events`));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Release history could not be loaded.");
    }
  }

  const pendingApprovals = (deployments.data || []).filter((item) => item.status.startsWith("awaiting_approval")).length;
  const activeReleases = (deployments.data || []).filter((item) => ["queued", "building", "deploying", "verifying", "validating"].includes(item.status)).length;
  const failedReleases = (deployments.data || []).filter((item) => item.status === "failed").length;

  return (
    <WorkspacePage eyebrow="WORKSPACE" title="Deployments" description="Queue releases, review approval gates and follow every durable state transition." actions={<button className="catalog-primary-button" onClick={() => setShowRequest((value) => !value)} disabled={!workspaceId || !canOperate}><Rocket size={14} /> Request release</button>}>
      <ResourceErrors resources={[{ label: "Deployments", error: deployments.error }, { label: "Build runs", error: builds.error }, { label: "Service catalog", error: services.error }, { label: "Environments", error: environments.error }]} />
      <div className="metric-grid metric-grid-3">
        <Metric label="Pending approvals" value={pendingApprovals} tone="amber" icon={<Clock3 size={16} />} />
        <Metric label="In progress" value={activeReleases} tone="cyan" icon={<Rocket size={16} />} />
        <Metric label="Failed releases" value={failedReleases} tone="red" icon={<CircleX size={16} />} />
      </div>

      {(notice || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || notice}<button className="notice-dismiss" onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss message"><X size={14} /></button></div>}

      {showRequest && <section className="settings-panel resource-form-panel">
        <div className="settings-panel-heading"><div className="settings-panel-icon"><Rocket size={16} /></div><div><h2>Request a release</h2><p>A durable deployment request will be validated and sent through build, policy and approval gates.</p></div></div>
        {!canOperate ? <p className="permission-note">Platform engineer or workspace administrator access is required to request releases.</p> : <form className="resource-form" onSubmit={(event) => void requestRelease(event)}>
          <label>Service<select required value={serviceId} onChange={(event) => setServiceId(event.target.value)}><option value="">Select a service</option>{(services.data?.items || []).map((service) => <option key={service.id} value={service.id}>{service.name} · {service.slug}</option>)}</select></label>
          <label>Environment<select required value={targetEnvironment} onChange={(event) => setTargetEnvironment(event.target.value)}><option value="">Select an environment</option>{environments.data?.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)}</select></label>
          <label>Version<input required value={version} onChange={(event) => setVersion(event.target.value)} placeholder="v2.8.1" maxLength={160} /></label>
          <label>Source ref <span className="field-optional">optional</span><input value={sourceRef} onChange={(event) => setSourceRef(event.target.value)} placeholder="main or commit SHA" maxLength={255} /></label>
          <button className="settings-primary-button" disabled={busyId === "new-release" || !serviceId || !targetEnvironment}><Rocket size={14} />{busyId === "new-release" ? "Queueing…" : "Queue release"}</button>
        </form>}
      </section>}

      <section className="resource-panel">
        <div className="resource-panel-heading"><div><div className="catalog-tabs" role="tablist"><button className={tab === "releases" ? "is-active" : ""} onClick={() => setTab("releases")} role="tab" aria-selected={tab === "releases"}>Release requests <span>{deployments.data?.length || 0}</span></button><button className={tab === "builds" ? "is-active" : ""} onClick={() => setTab("builds")} role="tab" aria-selected={tab === "builds"}>Build runs <span>{builds.data?.length || 0}</span></button></div><p>Live operational state from the release control plane</p></div><button className="catalog-secondary-button" onClick={() => void refresh()}><ArrowDownToLine size={13} /> Refresh</button></div>
        {tab === "releases" ? <>
          <LoadingOrEmpty loading={deployments.loading} empty={Boolean(deployments.data && deployments.data.length === 0)} label="No release requests in this workspace" />
          {deployments.data && deployments.data.length > 0 && <div className="resource-table-wrap"><table className="resource-table"><thead><tr><th>Release</th><th>Target</th><th>State</th><th>Requested</th><th>Attempts</th><th>Actions</th></tr></thead><tbody>{deployments.data.map((deployment) => {
            const service = serviceMap.get(deployment.service_id);
            const env = environmentMap.get(deployment.environment_id);
            const pending = deployment.status.startsWith("awaiting_approval");
            return <tr key={deployment.id}>
              <td><button className="resource-primary-cell" onClick={() => void openDetails(deployment)}><strong>{service?.name || `Service ${deployment.service_id.slice(0, 8)}`}</strong><small>{deployment.version}{deployment.source_ref ? ` · ${deployment.source_ref}` : ""}</small></button></td>
              <td><span className="resource-cell-strong">{env?.name || "Environment"}</span><small className="resource-subtext">{env?.environment_type || "—"}</small></td>
              <td><ResourceStatus value={deployment.status} />{deployment.failure_message && <small className="resource-subtext resource-failure">{deployment.failure_message}</small>}</td>
              <td>{formatDate(deployment.created_at)}</td><td>{deployment.attempt_count}/{deployment.max_attempts}</td>
              <td><div className="resource-row-actions">
                {pending && canOperate && <><button className="row-action approve" disabled={busyId === deployment.id} onClick={() => void runAction(deployment.id, () => apiPost(`/workspaces/${workspaceId}/deployments/${deployment.id}/approval`, { decision: "approve", comment: "Approved in DeployForge." }), "Release approved.")} title="Approve release"><Check size={14} /></button><button className="row-action reject" disabled={busyId === deployment.id} onClick={() => void runAction(deployment.id, () => apiPost(`/workspaces/${workspaceId}/deployments/${deployment.id}/approval`, { decision: "reject", comment: "Rejected in DeployForge." }), "Release rejected.")} title="Reject release"><X size={14} /></button></>}
                {deployment.status === "failed" && canOperate && <button className="row-action" disabled={busyId === deployment.id || deployment.attempt_count >= deployment.max_attempts} onClick={() => void runAction(deployment.id, () => apiPost(`/workspaces/${workspaceId}/deployments/${deployment.id}/retry`), "Release retry queued.")} title="Retry release"><RotateCcw size={14} /></button>}
                {["queued", "awaiting_approval", "awaiting_release_approval"].includes(deployment.status) && canOperate && <button className="row-action reject" disabled={busyId === deployment.id} onClick={() => void runAction(deployment.id, () => apiPost(`/workspaces/${workspaceId}/deployments/${deployment.id}/cancel`), "Release canceled.")} title="Cancel release"><CircleX size={14} /></button>}
                {deployment.status === "succeeded" && canOperate && <button className="row-action" disabled={busyId === deployment.id} onClick={() => { if (window.confirm(`Queue rollback for ${service?.name || "this service"} to its previous healthy revision?`)) void runAction(deployment.id, () => apiPost(`/workspaces/${workspaceId}/deployments/${deployment.id}/rollback`), "Rollback queued."); }} title="Rollback to previous healthy revision"><RotateCcw size={14} /></button>}
                {!pending && deployment.status !== "failed" && deployment.status !== "succeeded" && !["queued", "awaiting_approval", "awaiting_release_approval"].includes(deployment.status) && <span className="muted-cell">—</span>}
              </div></td>
            </tr>;
          })}</tbody></table></div>}
        </> : <>
          <LoadingOrEmpty loading={builds.loading} empty={Boolean(builds.data && builds.data.length === 0)} label="No CI build runs recorded" />
          {builds.data && builds.data.length > 0 && <div className="resource-table-wrap"><table className="resource-table"><thead><tr><th>Build</th><th>Provider</th><th>Source</th><th>State</th><th>Updated</th><th>Actions</th></tr></thead><tbody>{builds.data.map((build) => <tr key={build.id}><td><span className="resource-cell-strong">{serviceMap.get(build.service_id)?.name || "Service"}</span><small className="resource-subtext">Attempt {build.attempt_number} · {build.workflow_path}</small></td><td>{build.provider}</td><td><span className="resource-cell-strong">{build.source_ref}</span><small className="resource-subtext">Source {build.commit_sha?.slice(0, 12) || "commit pending"}</small>{build.gitops_revision && <small className="resource-subtext">GitOps {build.gitops_revision.slice(0, 12)}</small>}</td><td><ResourceStatus value={build.status} />{build.failure_message && <small className="resource-subtext resource-failure">{build.failure_message}</small>}{build.run_url && <a className="resource-inline-link" href={build.run_url} target="_blank" rel="noreferrer">External run ↗</a>}</td><td>{formatDate(build.updated_at)}</td><td>{build.status === "blocked" && canOperate ? <button className="row-action" disabled={busyId === build.id || build.attempt_count >= build.max_attempts} onClick={() => void runAction(build.id, () => apiPost(`/workspaces/${workspaceId}/build-runs/${build.id}/retry`), "Build retry queued.")} title="Retry blocked build"><RotateCcw size={14} /></button> : <span className="muted-cell">—</span>}</td></tr>)}</tbody></table></div>}
        </>}
      </section>

      {selected && <div className="resource-drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelected(null); }}><aside className="resource-drawer"><header><div><span className="eyebrow">RELEASE RECORD</span><h2>{selected.version}</h2></div><button className="icon-button" onClick={() => setSelected(null)} aria-label="Close release details"><X size={17} /></button></header><div className="drawer-summary"><ResourceStatus value={selected.status} /><span>Attempt {selected.attempt_count} of {selected.max_attempts}</span></div><div className="drawer-detail-grid"><span>Service</span><strong>{serviceMap.get(selected.service_id)?.name || selected.service_id}</strong><span>Environment</span><strong>{environmentMap.get(selected.environment_id)?.name || selected.environment_id}</strong><span>Source ref</span><strong>{selected.source_ref || "—"}</strong><span>Requested</span><strong>{formatDate(selected.created_at)}</strong><span>Updated</span><strong>{formatDate(selected.updated_at)}</strong></div><div className="drawer-section-heading"><GitCommitHorizontal size={15} /><strong>Immutable release timeline</strong></div>{events.length ? <ol className="timeline-list">{events.map((event) => <li key={event.id}><span className="timeline-marker" /><div><strong>{event.message}</strong><small>{event.event_type.replaceAll("_", " ")} · {formatDate(event.created_at)}</small></div></li>)}</ol> : <div className="resource-empty compact">{deployments.loading ? "Loading release history…" : "No release events available."}</div>}</aside></div>}
    </WorkspacePage>
  );
}

function Metric({ label, value, tone, icon }: { label: string; value: number; tone: string; icon: React.ReactNode }) {
  return <article className={`metric-card ${tone}`}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span></article>;
}

