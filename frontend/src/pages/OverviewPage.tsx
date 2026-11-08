import { useEffect, useMemo, useState } from "react";
import { Activity, ArrowRight, BellRing, Boxes, Clock3, GitBranch, Rocket, RotateCcw, Search, ShieldCheck, TrendingUp } from "lucide-react";
import { Link } from "react-router-dom";
import { PageFrame } from "../components/PageFrame";
import { StatusCards } from "../components/PlatformStatus";
import { usePlatformContext } from "../components/PlatformContext";
import { RecordDrawer, ViewAction, type RecordDetails } from "../components/RecordDrawer";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, TablePagination, useTableView, useWorkspaceResource } from "../components/WorkspacePage";
import { type AuditEvent, type Deployment, type ServiceCatalog, type ServiceCatalogSummary, type Slo, type SloAlert, type SystemStatus } from "../lib/api";

const inProgressStates = new Set(["queued", "validating", "policy_check", "approved", "building", "ready_to_deploy", "deploying", "verifying", "rollback_pending"]);
const reviewStates = new Set(["awaiting_approval", "awaiting_release_approval"]);
const releaseGroups = [
  { value: "in_progress", label: "In progress" },
  { value: "awaiting_review", label: "Pending approval" },
  { value: "succeeded", label: "Succeeded" },
  { value: "failed", label: "Failed" },
  { value: "rejected", label: "Rejected" },
  { value: "canceled", label: "Canceled" },
  { value: "rolled_back", label: "Rolled back" },
];

function releaseGroup(status: string) {
  if (inProgressStates.has(status)) return "in_progress";
  if (reviewStates.has(status)) return "awaiting_review";
  return status;
}

function inDateRange(value: string, start: string, end: string) {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp)
    && (!start || timestamp >= Date.parse(start + "T00:00:00Z"))
    && (!end || timestamp <= Date.parse(end + "T23:59:59.999Z"));
}

export default function OverviewPage() {
  const { workspaceId, environmentId } = usePlatformContext();
  const system = useWorkspaceResource<SystemStatus>("/system/status", 15_000);
  const catalog = useWorkspaceResource<ServiceCatalogSummary>(workspaceId ? "/workspaces/" + workspaceId + "/services/summary" : null);
  const services = useWorkspaceResource<ServiceCatalog>(workspaceId ? "/workspaces/" + workspaceId + "/services?limit=100" : null, 60_000, "catalog");
  const deploymentPath = workspaceId ? "/workspaces/" + workspaceId + "/deployments?limit=100" + (environmentId ? "&environment_id=" + encodeURIComponent(environmentId) : "") : null;
  const deployments = useWorkspaceResource<Deployment[]>(deploymentPath);
  const alerts = useWorkspaceResource<SloAlert[]>(workspaceId ? "/workspaces/" + workspaceId + "/alerts?limit=100" : null);
  const slos = useWorkspaceResource<Slo[]>(workspaceId ? "/workspaces/" + workspaceId + "/slos" : null, 60_000);
  const audit = useWorkspaceResource<AuditEvent[]>(workspaceId ? "/audit-logs?workspace_id=" + workspaceId + "&limit=10" : null);
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [serviceId, setServiceId] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [trendWindow, setTrendWindow] = useState(30);
  const [selectedRecord, setSelectedRecord] = useState<RecordDetails | null>(null);
  const serviceMap = useMemo(() => new Map((services.data?.items || []).map((service) => [service.id, service])), [services.data]);
  const sloMap = useMemo(() => new Map((slos.data || []).map((slo) => [slo.id, slo])), [slos.data]);
  const dateError = startDate && endDate && startDate > endDate ? "Start date must be on or before the end date." : "";
  const hasFilters = Boolean(query || serviceId || statusFilter || startDate || endDate);

  useEffect(() => {
    const timer = window.setTimeout(() => setAppliedQuery(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    setQuery(""); setAppliedQuery(""); setServiceId(""); setStatusFilter(""); setStartDate(""); setEndDate(""); setSelectedRecord(null);
  }, [workspaceId, environmentId]);

  const scopedReleases = useMemo(() => {
    if (dateError) return [];
    const normalized = appliedQuery.toLocaleLowerCase();
    return (deployments.data || []).filter((deployment) => {
      if (environmentId && deployment.environment_id !== environmentId) return false;
      if (serviceId && deployment.service_id !== serviceId) return false;
      if (!inDateRange(deployment.created_at, startDate, endDate)) return false;
      const searchable = [deployment.version, deployment.source_ref, deployment.id, serviceMap.get(deployment.service_id)?.name].filter(Boolean).join(" ").toLocaleLowerCase();
      return !normalized || searchable.includes(normalized);
    });
  }, [appliedQuery, dateError, deployments.data, endDate, environmentId, serviceId, serviceMap, startDate]);
  const filteredReleases = useMemo(() => scopedReleases.filter((deployment) => !statusFilter || releaseGroup(deployment.status) === statusFilter), [scopedReleases, statusFilter]);
  const activityReleases = useMemo(() => [...filteredReleases].sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at)), [filteredReleases]);
  const activityTable = useTableView(activityReleases, (deployment) => `${deployment.version} ${deployment.source_ref || ""} ${serviceMap.get(deployment.service_id)?.name || ""} ${deployment.status}`, {
    resetKey: `${workspaceId}:${environmentId}:${appliedQuery}:${serviceId}:${statusFilter}:${startDate}:${endDate}`,
  });
  const pendingApprovals = scopedReleases.filter((deployment) => reviewStates.has(deployment.status)).length;
  const inProgress = scopedReleases.filter((deployment) => inProgressStates.has(deployment.status)).length;
  const failedReleases = scopedReleases.filter((deployment) => deployment.status === "failed").length;
  const activeAlerts = (alerts.data || []).filter((alert) => {
    if (!["firing", "acknowledged"].includes(alert.status) || dateError) return false;
    const slo = sloMap.get(alert.slo_id);
    if (environmentId && (!slo || (slo.environment_id && slo.environment_id !== environmentId))) return false;
    if (serviceId && slo?.service_id !== serviceId) return false;
    return inDateRange(alert.created_at, startDate, endDate);
  });
  const trend = useMemo(() => {
    const today = new Date();
    const lastDay = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
    const firstDay = lastDay - (trendWindow - 1) * 86_400_000;
    const buckets = Array.from({ length: trendWindow }, (_, index) => ({
      date: new Date(firstDay + index * 86_400_000).toISOString().slice(0, 10),
      succeeded: 0, failed: 0, other: 0, total: 0,
    }));
    const bucketMap = new Map(buckets.map((bucket) => [bucket.date, bucket]));
    for (const deployment of filteredReleases) {
      const timestamp = Date.parse(deployment.created_at);
      if (!Number.isFinite(timestamp)) continue;
      const bucket = bucketMap.get(new Date(timestamp).toISOString().slice(0, 10));
      if (!bucket) continue;
      bucket.total += 1;
      if (deployment.status === "succeeded") bucket.succeeded += 1;
      else if (deployment.status === "failed") bucket.failed += 1;
      else bucket.other += 1;
    }
    return buckets;
  }, [filteredReleases, trendWindow]);
  const trendMax = Math.max(1, ...trend.map((bucket) => bucket.total));
  const trendTotal = trend.reduce((sum, bucket) => sum + bucket.total, 0);
  const metricValue = (value: number) => deployments.data && !dateError ? value : "—";

  function resetFilters() {
    setQuery(""); setAppliedQuery(""); setServiceId(""); setStatusFilter(""); setStartDate(""); setEndDate("");
  }

  function filterStatus(value: string) {
    setStatusFilter(value);
    document.getElementById("overview-release-activity")?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function viewRelease(deployment: Deployment) {
    setSelectedRecord({
      title: "Release " + deployment.version, eyebrow: "RELEASE DETAILS", status: deployment.status,
      description: serviceMap.get(deployment.service_id)?.name || deployment.service_id,
      sections: [
        { title: "Release request", fields: [{ label: "Version", value: deployment.version }, { label: "Source reference", value: deployment.source_ref }, { label: "Service", value: serviceMap.get(deployment.service_id)?.name || deployment.service_id }, { label: "Environment ID", value: deployment.environment_id, mono: true }, { label: "Requested by", value: deployment.requested_by, mono: true }, { label: "Attempts", value: deployment.attempt_count + " / " + deployment.max_attempts }] },
        { title: "Timeline", fields: [{ label: "Created", value: formatDate(deployment.created_at) }, { label: "Updated", value: formatDate(deployment.updated_at) }, { label: "Started", value: formatDate(deployment.started_at) }, { label: "Finished", value: formatDate(deployment.finished_at) }, { label: "Approved", value: formatDate(deployment.approved_at) }, { label: "Approved by", value: deployment.approved_by, mono: true }] },
        { title: "Record details", fields: [{ label: "Failure code", value: deployment.failure_code }, { label: "Failure message", value: deployment.failure_message }, { label: "Deployment ID", value: deployment.id, mono: true }] },
      ],
    });
  }

  return <PageFrame title="Platform overview" description="Live control plane health, service ownership and recent release activity in the selected workspace." eyebrow="CONTROL PLANE">
    <ResourceErrors resources={[{ label: "Control plane status", error: system.error }, { label: "Service catalog", error: catalog.error }, { label: "Services", error: services.error }, { label: "Deployments", error: deployments.error }, { label: "SLO alerts", error: alerts.error }, { label: "Service objectives", error: slos.error }, { label: "Audit history", error: audit.error }]} />
    {!workspaceId && <div className="workspace-empty"><Activity size={17} /><span><strong>Select a workspace</strong><small>Choose a workspace from the sidebar to view its operational records.</small></span></div>}
    <section className="overview-intro"><div className="intro-icon"><Activity size={19} /></div><div><strong>Control plane status</strong><p>Readiness and worker heartbeat are shown independently of the release filters below.</p></div><span className="live-indicator"><span className="status-dot" /> Refreshed every 15 seconds</span></section>
    <div className="section-heading"><div><h2>System components</h2><p>Control plane API, persistence and durable background processing</p></div><span className="live-indicator">{system.loading ? "Checking" : system.error ? "Status unavailable" : system.data ? "Checked " + formatDate(system.data.checked_at) : "Waiting for status"}</span></div>
    <StatusCards data={system.data || null} loading={system.loading} error={Boolean(system.error)} />

    <section className="resource-panel overview-filter-panel" aria-labelledby="overview-filter-title">
      <div className="resource-panel-heading"><div><h2 id="overview-filter-title">Release activity filters</h2><p>Search and date filters update automatically. Status cards select a release group.</p></div><Link className="resource-inline-link" to="/services"><Boxes size={14} />{catalog.data?.total ?? "—"} catalog services <ArrowRight size={13} /></Link></div>
      <div className="overview-filter-controls">
        <label className="table-search"><Search size={15} aria-hidden="true" /><span className="sr-only">Search overview releases</span><input type="search" value={query} disabled={!workspaceId} onChange={(event) => setQuery(event.target.value)} placeholder="Search version, service or source…" /></label>
        <label className="table-filter-field"><span>Service</span><select aria-label="Filter overview by service" value={serviceId} disabled={!workspaceId || services.loading} onChange={(event) => setServiceId(event.target.value)}><option value="">All services</option>{services.data?.items.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select></label>
        <label className="table-filter-field"><span>Release status</span><select aria-label="Filter overview by release status" value={statusFilter} disabled={!workspaceId} onChange={(event) => setStatusFilter(event.target.value)}><option value="">All statuses</option>{releaseGroups.map((group) => <option key={group.value} value={group.value}>{group.label}</option>)}</select></label>
        <label className="table-filter-field"><span>Created from (UTC)</span><input aria-label="Release start date in UTC" type="date" value={startDate} max={endDate || undefined} disabled={!workspaceId} onChange={(event) => setStartDate(event.target.value)} /></label>
        <label className="table-filter-field"><span>Created through (UTC)</span><input aria-label="Release end date in UTC" type="date" value={endDate} min={startDate || undefined} disabled={!workspaceId} onChange={(event) => setEndDate(event.target.value)} /></label>
      </div>
      {dateError && <p className="settings-notice is-error" role="alert">{dateError}</p>}
      <div className="table-filter-footer overview-filter-footer"><span role="status" aria-live="polite">{query.trim() !== appliedQuery ? "Updating release results…" : deployments.data ? filteredReleases.length + " matching release requests" : deployments.loading ? "Loading release requests…" : "Release data unavailable"}</span><button type="button" className="table-reset-filters" disabled={!hasFilters} onClick={resetFilters}><RotateCcw size={13} aria-hidden="true" />Reset filters</button></div>
    </section>
    <p className="overview-scope-note">Release cards, activity and trend use up to the newest 100 requests{environmentId ? " in the selected environment" : " in this workspace"}. Service, search and inclusive UTC date filters apply to every release card; status filters update the activity and trend.</p>
    <div className="metric-grid metric-grid-4">
      <Metric label="Recent releases" value={metricValue(scopedReleases.length)} icon={<Rocket size={16} />} tone="cyan" active={!statusFilter} onClick={() => filterStatus("")} />
      <Metric label="In progress" value={metricValue(inProgress)} icon={<Activity size={16} />} tone="violet" active={statusFilter === "in_progress"} onClick={() => filterStatus("in_progress")} />
      <Metric label="Pending approval" value={metricValue(pendingApprovals)} icon={<Clock3 size={16} />} tone="amber" active={statusFilter === "awaiting_review"} onClick={() => filterStatus("awaiting_review")} />
      <Metric label="Failed releases" value={metricValue(failedReleases)} icon={<TrendingUp size={16} />} tone="red" active={statusFilter === "failed"} onClick={() => filterStatus("failed")} />
    </div>

    <section className="resource-panel overview-trend-panel" aria-labelledby="overview-trend-title">
      <div className="resource-panel-heading"><div><h2 id="overview-trend-title">Release outcomes by creation day</h2><p>Current states of matching requests grouped by UTC creation date. These are not historical state snapshots.</p></div><label className="table-filter-field overview-trend-controls"><span>Trend window</span><select aria-label="Release trend window" value={trendWindow} onChange={(event) => setTrendWindow(Number(event.target.value))}><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></select></label></div>
      <div className="overview-trend-legend"><span className="trend-succeeded">Succeeded</span><span className="trend-failed">Failed</span><span className="trend-other">Other states</span><strong>{trendTotal} requests in this window</strong></div>
      {deployments.loading ? <LoadingOrEmpty loading empty={false} /> : !deployments.data ? <div className="resource-empty compact">Release data is unavailable.</div> : <>
        <div className="overview-trend-scroll" tabIndex={0} role="region" aria-label="Daily release outcomes. Use arrow keys to scroll the chart.">
          <div className="overview-trend-chart" role="img" aria-label={trendTotal + " matching release requests across the last " + trendWindow + " UTC days. The daily table below lists exact values."} style={{ minWidth: Math.max(360, trendWindow * 18), gridTemplateColumns: "repeat(" + trendWindow + ", minmax(0, 1fr))" }}>
            {trend.map((bucket, index) => <div className="overview-trend-day" key={bucket.date} title={bucket.date + ": " + bucket.succeeded + " succeeded, " + bucket.failed + " failed, " + bucket.other + " other"}>
              <div className="overview-trend-bar"><div className="overview-trend-stack" style={{ height: (bucket.total / trendMax * 100) + "%" }}>
                {bucket.succeeded > 0 && <span className="trend-succeeded" style={{ height: (bucket.succeeded / bucket.total * 100) + "%" }} />}
                {bucket.failed > 0 && <span className="trend-failed" style={{ height: (bucket.failed / bucket.total * 100) + "%" }} />}
                {bucket.other > 0 && <span className="trend-other" style={{ height: (bucket.other / bucket.total * 100) + "%" }} />}
              </div></div>
              <small aria-hidden="true">{index % Math.ceil(trendWindow / 7) === 0 || index === trendWindow - 1 ? bucket.date.slice(5) : "\u00a0"}</small>
            </div>)}
          </div>
        </div>
        {trendTotal === 0 && <p className="overview-scope-note">No matching release requests were created in this trend window.</p>}
        <details className="overview-trend-data"><summary>View daily data</summary><div className="resource-table-wrap"><table className="resource-table"><caption className="sr-only">Daily release creation dates and current outcome counts for the selected trend window</caption><thead><tr><th scope="col">UTC date</th><th scope="col">Succeeded</th><th scope="col">Failed</th><th scope="col">Other states</th><th scope="col">Total</th></tr></thead><tbody>{trend.map((bucket) => <tr key={bucket.date}><th scope="row">{bucket.date}</th><td>{bucket.succeeded}</td><td>{bucket.failed}</td><td>{bucket.other}</td><td>{bucket.total}</td></tr>)}</tbody></table></div></details>
      </>}
    </section>

    <div className="overview-columns">
      <section id="overview-release-activity" className="resource-panel"><div className="resource-panel-heading"><div><h2>Deployment activity</h2><p>Latest matching release requests in the current scope</p></div><Link className="resource-inline-link" to="/deployments">All deployments <ArrowRight size={13} /></Link></div>
        <LoadingOrEmpty loading={deployments.loading} empty={Boolean(deployments.data && activityReleases.length === 0)} label={hasFilters ? "No release requests match these filters" : "No deployments in this environment"} />
        {activityTable.pageItems.length > 0 && <div className="resource-table-wrap"><table className="resource-table has-actions"><caption className="sr-only">Recent release requests with service, state and record actions</caption><thead><tr><th scope="col">Release</th><th scope="col">Service</th><th scope="col">State</th><th scope="col">Actions</th></tr></thead><tbody>{activityTable.pageItems.map((deployment) => <tr key={deployment.id}><td><span className="resource-cell-strong">{deployment.version}</span><small className="resource-subtext">{deployment.source_ref || "Source ref not set"} · {formatDate(deployment.created_at)}</small></td><td>{serviceMap.get(deployment.service_id)?.name || deployment.service_id}</td><td><ResourceStatus value={deployment.status} /></td><td><ViewAction label={"release " + deployment.version} onClick={() => viewRelease(deployment)} /></td></tr>)}</tbody></table><TablePagination firstItem={activityTable.firstItem} lastItem={activityTable.lastItem} totalItems={activityTable.filteredItems.length} page={activityTable.page} pageCount={activityTable.pageCount} onPageChange={activityTable.setPage} label="release requests" /></div>}
      </section>
      <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Recent governance activity</h2><p>Latest ten workspace events, independent of release filters</p></div><Link className="resource-inline-link" to="/audit-logs">Audit logs <ArrowRight size={13} /></Link></div>
        <LoadingOrEmpty loading={audit.loading} empty={Boolean(audit.data && audit.data.length === 0)} label="No audit activity recorded" />
        {audit.data && audit.data.length > 0 && <div className="overview-activity-list">{audit.data.map((event) => <div className="overview-activity-row" key={event.id}><span className="overview-activity-icon governance"><ShieldCheck size={15} /></span><span className="overview-activity-copy"><strong>{event.action.replaceAll(".", " · ").replaceAll("_", " ")}</strong><small>{event.actor_id || "System"} · {formatDate(event.created_at)}</small></span><ResourceStatus value={event.outcome} /></div>)}</div>}
      </section>
    </div>
    {activeAlerts.length > 0 && <Link className="overview-alert-banner" to="/incidents"><span className="overview-alert-icon"><BellRing size={16} /></span><span><strong>{alerts.data?.length === 100 ? "At least " : ""}{activeAlerts.length} active SLO alert{activeAlerts.length === 1 ? "" : "s"}</strong><small>Recent alerts in this service and date scope. Review the incident response queue.</small></span><ArrowRight size={15} /></Link>}
    <section className="overview-module-strip"><div><h2>Platform modules</h2><p>Move from service ownership to delivery and reliability operations.</p></div><div className="module-shortcuts"><Shortcut to="/services" icon={<Boxes size={15} />} label="Service Catalog" /><Shortcut to="/deployments" icon={<Rocket size={15} />} label="Deployments" /><Shortcut to="/gitops" icon={<GitBranch size={15} />} label="GitOps" /><Shortcut to="/policies" icon={<ShieldCheck size={15} />} label="Policies" /></div></section>
    {selectedRecord && <RecordDrawer record={selectedRecord} onClose={() => setSelectedRecord(null)} />}
  </PageFrame>;
}

function Metric({ label, value, icon, tone, active, onClick }: { label: string; value: number | string; icon: React.ReactNode; tone: string; active: boolean; onClick: () => void }) {
  return <button type="button" className={"metric-card metric-filter-card " + tone + (active ? " is-active" : "")} aria-label={"Show " + label.toLowerCase() + ": " + value} aria-pressed={active} onClick={onClick}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span><ArrowRight className="metric-action-icon" size={15} /></button>;
}
function Shortcut({ to, icon, label }: { to: string; icon: React.ReactNode; label: string }) { return <Link to={to} className="module-shortcut">{icon}<span>{label}</span><ArrowRight size={13} /></Link>; }
