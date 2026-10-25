import { useMemo } from "react";
import { Activity, ArrowRight, BellRing, Boxes, GitBranch, Rocket, ShieldCheck, TrendingUp } from "lucide-react";
import { Link } from "react-router-dom";
import { PageFrame } from "../components/PageFrame";
import { StatusCards } from "../components/PlatformStatus";
import { usePlatformContext } from "../components/PlatformContext";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, useWorkspaceResource } from "../components/WorkspacePage";
import { type AuditEvent, type Deployment, type ServiceCatalogSummary, type Slo, type SloAlert, type SystemStatus } from "../lib/api";

export default function OverviewPage() {
  const { workspaceId, environmentId } = usePlatformContext();
  const system = useWorkspaceResource<SystemStatus>("/system/status", 15_000);
  const catalog = useWorkspaceResource<ServiceCatalogSummary>(workspaceId ? `/workspaces/${workspaceId}/services/summary` : null);
  const deployments = useWorkspaceResource<Deployment[]>(workspaceId ? `/workspaces/${workspaceId}/deployments?limit=50` : null);
  const alerts = useWorkspaceResource<SloAlert[]>(workspaceId ? `/workspaces/${workspaceId}/alerts?limit=100` : null);
  const slos = useWorkspaceResource<Slo[]>(workspaceId ? `/workspaces/${workspaceId}/slos` : null, 60_000);
  const audit = useWorkspaceResource<AuditEvent[]>(workspaceId ? `/audit-logs?workspace_id=${workspaceId}&limit=8` : null);
  const status = system.data;
  const activeAlerts = alerts.data?.filter((alert) => {
    if (alert.status !== "firing" && alert.status !== "acknowledged") return false;
    if (!environmentId) return true;
    const slo = slos.data?.find((item) => item.id === alert.slo_id);
    return !slo?.environment_id || slo.environment_id === environmentId;
  }) || [];
  const scopedDeployments = useMemo(() => (deployments.data || []).filter((deployment) => !environmentId || deployment.environment_id === environmentId), [deployments.data, environmentId]);
  const pendingApprovals = scopedDeployments.filter((deployment) => ["awaiting_approval", "awaiting_release_approval"].includes(deployment.status)).length;
  const failedReleases = scopedDeployments.filter((deployment) => deployment.status === "failed").length;
  const recentDeployments = useMemo(() => scopedDeployments.slice(0, 5), [scopedDeployments]);

  return <PageFrame title="Platform overview" description="A live view of control plane health, release activity and service ownership in the selected workspace." eyebrow="CONTROL PLANE">
    <ResourceErrors resources={[{ label: "Control plane status", error: system.error }, { label: "Service catalog", error: catalog.error }, { label: "Deployments", error: deployments.error }, { label: "SLO alerts", error: alerts.error }, { label: "Service objectives", error: slos.error }, { label: "Audit history", error: audit.error }]} />
    {!workspaceId && <div className="workspace-empty"><Activity size={17} /><span><strong>Select a workspace</strong><small>Choose a workspace from the sidebar to view its operational records.</small></span></div>}
    <section className="overview-intro"><div className="intro-icon"><Activity size={19} /></div><div><strong>Production platform status</strong><p>Live health comes from the API readiness endpoint and worker heartbeat. Operational records below are loaded from the selected workspace.</p></div><span className="live-indicator"><span className="status-dot" /> Refreshed every 15 seconds</span></section>
    <div className="section-heading"><div><h2>System components</h2><p>Control plane API, persistence and durable background processing</p></div><span className="live-indicator">{system.loading ? "Checking" : system.error ? "Status unavailable" : status ? `Checked ${new Date(status.checked_at).toLocaleTimeString()}` : "Waiting for status"}</span></div>
    <StatusCards data={status || null} loading={system.loading} error={Boolean(system.error)} />
    <div className="metric-grid metric-grid-4"><Metric label="Catalog services" value={catalog.data?.total ?? "—"} icon={<Boxes size={16} />} tone="cyan" /><Metric label="Active deployments" value={pendingApprovals + scopedDeployments.filter((deployment) => ["queued", "building", "deploying", "verifying"].includes(deployment.status)).length} icon={<Rocket size={16} />} tone="violet" /><Metric label="Active SLO alerts" value={activeAlerts.length} icon={<BellRing size={16} />} tone={activeAlerts.length ? "red" : "green"} /><Metric label="Failed releases" value={failedReleases} icon={<TrendingUp size={16} />} tone={failedReleases ? "amber" : "green"} /></div>

    <div className="overview-columns">
      <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Deployment activity</h2><p>Newest durable release requests for this workspace</p></div><Link className="resource-inline-link" to="/deployments">All deployments <ArrowRight size={13} /></Link></div>
        <LoadingOrEmpty loading={deployments.loading} empty={Boolean(deployments.data && deployments.data.length === 0)} label="No deployments have been requested" />
        {recentDeployments.length > 0 && <div className="overview-activity-list">{recentDeployments.map((deployment) => <Link className="overview-activity-row" to="/deployments" key={deployment.id}><span className="overview-activity-icon"><Rocket size={15} /></span><span className="overview-activity-copy"><strong>Release {deployment.version}</strong><small>{deployment.source_ref || "Source ref not set"} · {formatDate(deployment.created_at)}</small></span><ResourceStatus value={deployment.status} /></Link>)}</div>}
      </section>
      <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Recent governance activity</h2><p>Latest workspace-scoped user and operator events</p></div><Link className="resource-inline-link" to="/audit-logs">Audit logs <ArrowRight size={13} /></Link></div>
        <LoadingOrEmpty loading={audit.loading} empty={Boolean(audit.data && audit.data.length === 0)} label="No audit activity recorded" />
        {audit.data && audit.data.length > 0 && <div className="overview-activity-list">{audit.data.map((event) => <div className="overview-activity-row" key={event.id}><span className="overview-activity-icon governance"><ShieldCheck size={15} /></span><span className="overview-activity-copy"><strong>{event.action.replaceAll(".", " · ").replaceAll("_", " ")}</strong><small>{event.actor_id || "System"} · {formatDate(event.created_at)}</small></span><ResourceStatus value={event.outcome} /></div>)}</div>}
      </section>
    </div>
    {activeAlerts.length > 0 && <Link className="overview-alert-banner" to="/incidents"><span className="overview-alert-icon"><BellRing size={16} /></span><span><strong>{activeAlerts.length} active SLO alert{activeAlerts.length === 1 ? "" : "s"}</strong><small>Review reliability signals in the incident response queue.</small></span><ArrowRight size={15} /></Link>}
    <section className="overview-module-strip"><div><h2>Platform modules</h2><p>Move from service ownership to delivery and reliability operations.</p></div><div className="module-shortcuts"><Shortcut to="/services" icon={<Boxes size={15} />} label="Service Catalog" /><Shortcut to="/deployments" icon={<Rocket size={15} />} label="Deployments" /><Shortcut to="/gitops" icon={<GitBranch size={15} />} label="GitOps" /><Shortcut to="/policies" icon={<ShieldCheck size={15} />} label="Policies" /></div></section>
  </PageFrame>;
}

function Metric({ label, value, icon, tone }: { label: string; value: number | string; icon: React.ReactNode; tone: string }) { return <article className={`metric-card ${tone}`}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span></article>; }
function Shortcut({ to, icon, label }: { to: string; icon: React.ReactNode; label: string }) { return <Link to={to} className="module-shortcut">{icon}<span>{label}</span><ArrowRight size={13} /></Link>; }
