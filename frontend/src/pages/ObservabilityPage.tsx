import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Activity, ArrowUpRight, BellRing, Check, Gauge, KeyRound, Plus, Radio, ShieldCheck, X } from "lucide-react";
import { apiGet, apiPatch, apiPost, type Environment, type ServiceCatalog, type Slo, type SloAlert, type SloEvaluation, type TelemetrySource } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { FieldLabel, FormDrawer } from "../components/FormDrawer";
import { slugFromName } from "../lib/forms";
import { RecordDrawer, ViewAction, type RecordDetails } from "../components/RecordDrawer";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, TablePagination, TableToolbar, useTableView, useWorkspaceResource, WorkspacePage } from "../components/WorkspacePage";

export default function ObservabilityPage() {
  const { workspaceId, environmentId, canOperate } = usePlatformContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedAlertId = searchParams.get("alert");
  const activeWorkspace = useRef(workspaceId);
  activeWorkspace.current = workspaceId;
  const slos = useWorkspaceResource<Slo[]>(workspaceId ? `/workspaces/${workspaceId}/slos` : null);
  const alerts = useWorkspaceResource<SloAlert[]>(workspaceId ? `/workspaces/${workspaceId}/alerts?limit=200` : null, 20_000, "array");
  const sources = useWorkspaceResource<TelemetrySource[]>(workspaceId ? `/workspaces/${workspaceId}/telemetry-sources` : null);
  const services = useWorkspaceResource<ServiceCatalog>(workspaceId ? `/workspaces/${workspaceId}/services?limit=100` : null, 60_000, "catalog");
  const environments = useWorkspaceResource<Environment[]>(workspaceId ? `/workspaces/${workspaceId}/environments` : null, 60_000);
  const [evaluations, setEvaluations] = useState<Record<string, SloEvaluation | null>>({});
  const [showSlo, setShowSlo] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const [sloService, setSloService] = useState("");
  const [sloEnvironment, setSloEnvironment] = useState("");
  const [sloName, setSloName] = useState("");
  const [sloSlug, setSloSlug] = useState("");
  const [sloSlugEdited, setSloSlugEdited] = useState(false);
  const [sloKind, setSloKind] = useState<"availability" | "latency">("availability");
  const [objective, setObjective] = useState("99.9");
  const [windowHours, setWindowHours] = useState("24");
  const [latency, setLatency] = useState("300");
  const [sourceName, setSourceName] = useState("");
  const [sourceService, setSourceService] = useState("");
  const [sourceEnvironment, setSourceEnvironment] = useState("");
  const [createdToken, setCreatedToken] = useState("");
  const [tokenCopied, setTokenCopied] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [selectedRecord, setSelectedRecord] = useState<RecordDetails | null>(null);
  const serviceMap = useMemo(() => new Map((services.data?.items || []).map((service) => [service.id, service])), [services.data]);
  const environmentMap = useMemo(() => new Map((environments.data || []).map((environment) => [environment.id, environment])), [environments.data]);
  const displaySlos = useMemo(() => (slos.data || []).filter((slo) => !environmentId || !slo.environment_id || slo.environment_id === environmentId), [environmentId, slos.data]);
  const displaySources = useMemo(() => (sources.data || []).filter((source) => !environmentId || !source.environment_id || source.environment_id === environmentId), [environmentId, sources.data]);

  function resetSloForm() {
    setSloName(""); setSloSlug(""); setSloSlugEdited(false); setSloService(""); setSloEnvironment(""); setSloKind("availability"); setObjective("99.9"); setWindowHours("24"); setLatency("300"); setError("");
  }

  function resetSourceForm() {
    setSourceName(""); setSourceService(""); setSourceEnvironment(""); setError("");
  }

  useEffect(() => {
    setShowSlo(false); setShowSource(false); resetSloForm(); resetSourceForm();
    setCreatedToken(""); setTokenCopied(false);
  }, [workspaceId]);

  useEffect(() => {
    if (!workspaceId || !slos.data?.length) { setEvaluations({}); return; }
    let active = true;
    const controller = new AbortController();
    void Promise.all(slos.data.map(async (slo) => {
      try { return [slo.id, await apiGet<SloEvaluation | null>(`/workspaces/${workspaceId}/slos/${slo.id}/current`, controller.signal)] as const; }
      catch { return [slo.id, null] as const; }
    })).then((values) => { if (active) setEvaluations(Object.fromEntries(values)); });
    return () => { active = false; controller.abort(); };
  }, [slos.data, workspaceId]);

  async function act(id: string, operation: () => Promise<unknown>, message: string) {
    if (busy) return;
    setBusy(id); setError(""); setNotice("");
    try { await operation(); if (workspaceId !== activeWorkspace.current) return; setNotice(message); slos.refresh(); alerts.refresh(); sources.refresh(); }
    catch (caught) { if (workspaceId === activeWorkspace.current) setError(caught instanceof Error ? caught.message : "The observability action could not be completed."); }
    finally { setBusy(""); }
  }

  async function createSlo(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || busy === "new-slo") return;
    const objectivePercent = Number(objective);
    const hours = Number(windowHours);
    if (!Number.isFinite(objectivePercent) || objectivePercent <= 0 || objectivePercent >= 100) { setError("Objective must be greater than 0% and below 100%."); return; }
    if (!Number.isInteger(hours) || hours < 1 || hours > 720) { setError("Window must be a whole number from 1 to 720 hours."); return; }
    await act("new-slo", async () => {
      await apiPost(`/workspaces/${workspaceId}/slos`, { slug: sloSlug.trim(), name: sloName.trim(), service_id: sloService, environment_id: sloEnvironment || undefined, indicator: sloKind, objective: objectivePercent / 100, window_hours: hours, latency_threshold_ms: sloKind === "latency" ? Number(latency) : undefined });
      resetSloForm(); setShowSlo(false);
    }, "Service level objective created.");
  }

  async function createSource(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || busy === "new-source") return;
    await act("new-source", async () => {
      const result = await apiPost<TelemetrySource & { token: string }>(`/workspaces/${workspaceId}/telemetry-sources`, { name: sourceName.trim(), service_id: sourceService, environment_id: sourceEnvironment || undefined });
      if (workspaceId !== activeWorkspace.current) return;
      setCreatedToken(result.token); setTokenCopied(false); resetSourceForm(); setShowSource(false);
    }, "Telemetry source created. Copy its credential now; it will not be shown again.");
  }

  async function copyToken() {
    try { await navigator.clipboard.writeText(createdToken); setTokenCopied(true); }
    catch { setError("Clipboard access is unavailable. Select and copy the credential manually."); }
  }

  const displayAlerts = useMemo(() => (alerts.data || []).filter((alert) => {
    if (!environmentId) return true;
    const slo = slos.data?.find((item) => item.id === alert.slo_id);
    return !slo?.environment_id || slo.environment_id === environmentId;
  }), [alerts.data, environmentId, slos.data]);
  const activeAlerts = displayAlerts.filter((alert) => alert.status === "firing" || alert.status === "acknowledged");
  const serviceOptions = (services.data?.items || []).map((service) => ({ value: service.id, label: service.name }));
  const resetKey = `${workspaceId}:${environmentId}`;
  const slosTable = useTableView(displaySlos, (slo) => `${slo.name} ${serviceMap.get(slo.service_id)?.name || ""} ${slo.indicator} ${environmentMap.get(slo.environment_id || "")?.name || ""}`, {
    resetKey,
    filters: [
      { id: "status", label: "Status", getValue: (slo) => evaluations[slo.id]?.status || "no_data", options: [{ value: "healthy", label: "Healthy" }, { value: "breaching", label: "Breaching" }, { value: "no_data", label: "No data" }] },
      { id: "indicator", label: "Indicator", getValue: (slo) => slo.indicator, options: [{ value: "availability", label: "Availability" }, { value: "latency", label: "Latency" }] },
      { id: "service", label: "Service", getValue: (slo) => slo.service_id, options: serviceOptions },
    ],
  });
  const alertsTable = useTableView(displayAlerts, (alert) => `${alert.summary} ${alert.status} ${alert.slo_id} ${slos.data?.find((slo) => slo.id === alert.slo_id)?.name || ""}`, {
    resetKey,
    filters: [
      { id: "status", label: "Status", getValue: (alert) => alert.status, options: [{ value: "firing", label: "Firing" }, { value: "acknowledged", label: "Acknowledged" }, { value: "resolved", label: "Resolved" }] },
      { id: "activity", label: "Response", getValue: (alert) => ["firing", "acknowledged"].includes(alert.status) ? "active" : "closed", options: [{ value: "active", label: "Active" }, { value: "closed", label: "Closed" }] },
      { id: "service", label: "Service", getValue: (alert) => slos.data?.find((slo) => slo.id === alert.slo_id)?.service_id, options: serviceOptions },
    ],
  });
  const sourcesTable = useTableView(displaySources, (source) => `${source.name} ${serviceMap.get(source.service_id)?.name || ""} ${environmentMap.get(source.environment_id || "")?.name || ""} ${source.token_prefix} ${source.enabled ? "enabled" : "revoked"}`, {
    resetKey,
    filters: [
      { id: "status", label: "Status", getValue: (source) => source.enabled ? "enabled" : "revoked", options: [{ value: "enabled", label: "Enabled" }, { value: "revoked", label: "Revoked" }] },
      { id: "service", label: "Service", getValue: (source) => source.service_id, options: serviceOptions },
    ],
  });
  const breaching = displaySlos.filter((slo) => evaluations[slo.id]?.status === "breaching").length;
  const averageBudget = displaySlos.flatMap((slo) => {
    const budget = evaluations[slo.id]?.error_budget_remaining;
    return budget === null || budget === undefined ? [] : [budget];
  });
  const budgetPercent = averageBudget.length ? Math.round(averageBudget.reduce((sum, item) => sum + item, 0) / averageBudget.length * 100) : null;

  useEffect(() => { setSelectedRecord(null); }, [workspaceId, environmentId]);

  useEffect(() => {
    if (!requestedAlertId || !workspaceId || !alerts.data || !slos.data || !services.data || !environments.data) return;
    const alert = alerts.data.find((item) => item.id === requestedAlertId);
    if (alert) viewAlert(alert);
    else setError("The alert selected from search is no longer available in this workspace.");
    const next = new URLSearchParams(searchParams);
    next.delete("alert");
    setSearchParams(next, { replace: true });
  }, [requestedAlertId, workspaceId, alerts.data, slos.data, services.data, environments.data, searchParams, setSearchParams]);

  function showObjectives(status = "") {
    slosTable.resetFilters();
    if (status) slosTable.setFilterValue("status", status);
    document.getElementById("observability-objectives")?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function showActiveAlerts() {
    alertsTable.resetFilters();
    alertsTable.setFilterValue("activity", "active");
    document.getElementById("observability-alerts")?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function viewSlo(slo: Slo) {
    const current = evaluations[slo.id];
    setSelectedRecord({ title: slo.name, eyebrow: "SERVICE LEVEL OBJECTIVE", description: slo.slug, status: current?.status || "no_data", sections: [
      { title: "Objective scope", fields: [{ label: "Service", value: serviceMap.get(slo.service_id)?.name || slo.service_id }, { label: "Environment", value: slo.environment_id ? environmentMap.get(slo.environment_id)?.name || slo.environment_id : "All environments" }, { label: "Indicator", value: slo.indicator }, { label: "Target", value: `${(slo.objective * 100).toFixed(2)}%` }, { label: "Window", value: `${slo.window_hours} hours` }, ...(slo.latency_threshold_ms === null ? [] : [{ label: "Latency threshold", value: `${slo.latency_threshold_ms} ms` }]), { label: "Enabled", value: slo.enabled ? "Yes" : "No" }] },
      { title: "Current evaluation", fields: [{ label: "Attainment", value: current?.attainment === null || current?.attainment === undefined ? "No data" : `${(current.attainment * 100).toFixed(3)}%` }, { label: "Error budget remaining", value: current?.error_budget_remaining === null || current?.error_budget_remaining === undefined ? "No data" : `${(current.error_budget_remaining * 100).toFixed(1)}%` }, { label: "Burn rate", value: current?.burn_rate === null || current?.burn_rate === undefined ? "No data" : `${current.burn_rate.toFixed(2)}×` }, { label: "Total events", value: current?.total_events }, { label: "Good events", value: current?.good_events }, { label: "Window start", value: formatDate(current?.window_start) }, { label: "Window end", value: formatDate(current?.window_end) }] },
      { title: "Record metadata", fields: [{ label: "Created", value: formatDate(slo.created_at) }, { label: "Last evaluated", value: formatDate(slo.last_evaluated_at) }, { label: "Created by", value: slo.created_by, mono: true }, { label: "Objective ID", value: slo.id, mono: true }] },
    ] });
  }

  function viewAlert(alert: SloAlert) {
    const slo = slos.data?.find((item) => item.id === alert.slo_id);
    setSelectedRecord({ title: alert.summary, eyebrow: "SLO ALERT", status: alert.status, sections: [
      { title: "Alert scope", fields: [{ label: "Objective", value: slo?.name || alert.slo_id }, { label: "Service", value: slo ? serviceMap.get(slo.service_id)?.name || slo.service_id : undefined }, { label: "Environment", value: slo ? slo.environment_id ? environmentMap.get(slo.environment_id)?.name || slo.environment_id : "All environments" : undefined }, { label: "Opened", value: formatDate(alert.created_at) }] },
      { title: "Response timeline", fields: [{ label: "Acknowledged", value: formatDate(alert.acknowledged_at) }, { label: "Acknowledged by", value: alert.acknowledged_by, mono: true }, { label: "Resolved", value: formatDate(alert.resolved_at) }] },
      ...(Object.keys(alert.details).length ? [{ title: "Evaluation details", fields: Object.entries(alert.details).map(([key, value]) => ({ label: key.replaceAll("_", " "), value: detailValue(value) })) }] : []),
      { title: "Record identifiers", fields: [{ label: "Alert ID", value: alert.id, mono: true }, { label: "Objective ID", value: alert.slo_id, mono: true }, { label: "Evaluation ID", value: alert.evaluation_id, mono: true }] },
    ] });
  }

  function viewSource(source: TelemetrySource) {
    setSelectedRecord({ title: source.name, eyebrow: "TELEMETRY SOURCE", sections: [
      { title: "Source scope", fields: [{ label: "Service", value: serviceMap.get(source.service_id)?.name || source.service_id }, { label: "Environment", value: source.environment_id ? environmentMap.get(source.environment_id)?.name || source.environment_id : "All environments" }, { label: "Status", value: source.enabled ? "Enabled" : "Revoked" }, { label: "Token prefix", value: source.token_prefix, mono: true }] },
      { title: "Activity", fields: [{ label: "Created", value: formatDate(source.created_at) }, { label: "Last seen", value: source.last_seen_at ? formatDate(source.last_seen_at) : "Never connected" }, { label: "Created by", value: source.created_by, mono: true }, { label: "Source ID", value: source.id, mono: true }] },
    ] });
  }

  return <WorkspacePage eyebrow="RELIABILITY" title="Observability" description="Connect service telemetry, define SLOs and respond to live reliability signals." actions={<><button className="catalog-secondary-button" onClick={() => { resetSourceForm(); setShowSource(true); }} disabled={!workspaceId || !canOperate || Boolean(busy)}><Radio size={13} /> Telemetry source</button><button className="catalog-primary-button" onClick={() => { resetSloForm(); setShowSlo(true); }} disabled={!workspaceId || !canOperate || Boolean(busy)}><Plus size={14} /> Create SLO</button></>}>
    <ResourceErrors resources={[{ label: "Service objectives", error: slos.error }, { label: "SLO alerts", error: alerts.error }, { label: "Telemetry sources", error: sources.error }, { label: "Services", error: services.error }, { label: "Environments", error: environments.error }]} />
    <div className="metric-grid metric-grid-4"><Metric label="Service objectives" value={displaySlos.length} icon={<Gauge size={16} />} tone="cyan" active={!slosTable.hasActiveFilters} onClick={() => showObjectives()} /><Metric label="Breaching objectives" value={breaching} icon={<Activity size={16} />} tone={breaching ? "red" : "green"} active={slosTable.filters.some((filter) => filter.id === "status" && filter.value === "breaching")} onClick={() => showObjectives("breaching")} /><Metric label="Active alerts" value={activeAlerts.length} icon={<BellRing size={16} />} tone={activeAlerts.length ? "amber" : "green"} active={alertsTable.filters.some((filter) => filter.id === "activity" && filter.value === "active")} onClick={showActiveAlerts} /><Metric label="Avg. error budget" value={budgetPercent === null ? "—" : `${budgetPercent}%`} icon={<ShieldCheck size={16} />} tone="violet" /></div>
    {(notice || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || notice}<button className="notice-dismiss" onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss message"><X size={14} /></button></div>}
    {createdToken && <section className="credential-panel"><div className="credential-panel-icon"><KeyRound size={16} /></div><div><strong>One-time telemetry credential</strong><p>Use this bearer token in your telemetry exporter. Store it in a secrets manager; it cannot be retrieved later.</p><code>{createdToken}</code></div><button className="catalog-secondary-button" onClick={() => void copyToken()}>{tokenCopied ? "Copied" : "Copy token"}</button><button className="icon-button" onClick={() => { setCreatedToken(""); setTokenCopied(false); }} aria-label="Dismiss credential"><X size={15} /></button></section>}
    {showSlo && <FormDrawer title="Define a service level objective" description="Objectives are evaluated automatically by the worker every minute." dirty={Boolean(sloName || sloSlug || sloService || sloEnvironment || sloKind !== "availability" || objective !== "99.9" || windowHours !== "24" || latency !== "300")} onClose={() => { resetSloForm(); setShowSlo(false); }} onReset={resetSloForm} busy={busy === "new-slo"} error={error} actions={canOperate && <button form="create-slo-form" type="submit" className="settings-primary-button" disabled={busy === "new-slo" || !sloName.trim() || !sloService}><Plus size={14} />{busy === "new-slo" ? "Creating…" : "Create objective"}</button>}>{!canOperate ? <p className="permission-note">Platform engineer or workspace administrator access is required.</p> : <form id="create-slo-form" className="resource-form" onSubmit={(event) => void createSlo(event)}>
      <label><FieldLabel required>Name</FieldLabel><input required minLength={2} maxLength={160} value={sloName} onChange={(event) => { setSloName(event.target.value); if (!sloSlugEdited) setSloSlug(slugFromName(event.target.value, 100)); }} placeholder="Checkout availability" /></label>
      <label><FieldLabel required>Objective slug</FieldLabel><input required maxLength={100} pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" value={sloSlug} onChange={(event) => { setSloSlug(event.target.value); setSloSlugEdited(true); }} placeholder="checkout-availability" aria-describedby="objective-slug-hint" /></label>
      <p id="objective-slug-hint" className="form-note form-wide">Use lowercase letters, numbers and hyphens for the objective identifier. Review the suggestion before creating it.</p>
      <label><FieldLabel required>Service</FieldLabel><select required value={sloService} onChange={(event) => setSloService(event.target.value)}><option value="">Select service</option>{services.data?.items.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select></label>
      <label><FieldLabel optional>Environment scope</FieldLabel><select value={sloEnvironment} onChange={(event) => setSloEnvironment(event.target.value)}><option value="">All environments</option>{environments.data?.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)}</select></label>
      <label><FieldLabel required>Indicator</FieldLabel><select required value={sloKind} onChange={(event) => setSloKind(event.target.value as "availability" | "latency")}><option value="availability">Availability</option><option value="latency">Latency threshold</option></select></label>
      <label><FieldLabel required>Objective (%)</FieldLabel><input type="number" min="0" max="100" step="any" required value={objective} onChange={(event) => setObjective(event.target.value)} aria-describedby="objective-range-hint" /></label>
      <label><FieldLabel required>Window (hours)</FieldLabel><input type="number" min="1" max="720" step="1" required value={windowHours} onChange={(event) => setWindowHours(event.target.value)} /></label>
      {sloKind === "latency" && <label><FieldLabel required>Latency threshold</FieldLabel><select required value={latency} onChange={(event) => setLatency(event.target.value)}>{[100, 300, 500, 1000, 2500, 5000].map((value) => <option key={value} value={value}>{value} ms</option>)}</select></label>}
      <p id="objective-range-hint" className="form-note form-wide">The objective must be greater than 0% and below 100%.</p>
    </form>}</FormDrawer>}
    {showSource && <FormDrawer title="Register a telemetry source" description="Credentials are bound to one service and can optionally be limited to one environment." dirty={Boolean(sourceName || sourceService || sourceEnvironment)} onClose={() => { resetSourceForm(); setShowSource(false); }} onReset={resetSourceForm} busy={busy === "new-source"} error={error} actions={canOperate && <button form="create-telemetry-source-form" type="submit" className="settings-primary-button" disabled={busy === "new-source" || !sourceName.trim() || !sourceService}><KeyRound size={14} />{busy === "new-source" ? "Creating…" : "Create credential"}</button>}>{!canOperate ? <p className="permission-note">Platform engineer or workspace administrator access is required.</p> : <form id="create-telemetry-source-form" className="resource-form" onSubmit={(event) => void createSource(event)}><label><FieldLabel required>Source name</FieldLabel><input required minLength={2} maxLength={120} value={sourceName} onChange={(event) => setSourceName(event.target.value)} placeholder="checkout-otel-collector" /></label><label><FieldLabel required>Service</FieldLabel><select required value={sourceService} onChange={(event) => setSourceService(event.target.value)}><option value="">Select service</option>{services.data?.items.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select></label><label><FieldLabel optional>Environment</FieldLabel><select value={sourceEnvironment} onChange={(event) => setSourceEnvironment(event.target.value)}><option value="">All environments</option>{environments.data?.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)}</select></label></form>}</FormDrawer>}
    <section id="observability-objectives" className="resource-panel">
      <div className="resource-panel-heading"><div><h2>Service level objectives</h2><p>Attainment, error budget and burn rate are computed from ingested service telemetry.</p></div><span className="live-indicator"><span className="status-dot" /> Evaluated by worker</span></div>
      <LoadingOrEmpty loading={slos.loading} empty={Boolean(slos.data && displaySlos.length === 0)} label="No service level objectives defined for this environment" />
      {displaySlos.length > 0 && <TableToolbar query={slosTable.query} onQueryChange={slosTable.setQuery} filteredCount={slosTable.filteredItems.length} totalCount={slosTable.totalItems} pageSize={slosTable.pageSize} onPageSizeChange={slosTable.setPageSize} filters={slosTable.filters} onFilterChange={slosTable.setFilterValue} onResetFilters={slosTable.resetFilters} isFiltering={slosTable.isFiltering} label="service objectives" />}
      {displaySlos.length > 0 && slosTable.filteredItems.length === 0 && <div className="resource-empty compact">No objectives match these filters.</div>}
      {slosTable.pageItems.length > 0 && <div className="resource-table-wrap"><table className="resource-table has-actions">
        <caption className="sr-only">Service objectives, service scope, targets, current attainment, error budget, status and actions</caption>
        <thead><tr><th scope="col">Objective</th><th scope="col">Service scope</th><th scope="col">Target</th><th scope="col">Current attainment</th><th scope="col">Error budget</th><th scope="col">Status</th><th scope="col">Actions</th></tr></thead>
        <tbody>{slosTable.pageItems.map((slo) => { const current = evaluations[slo.id]; return <tr key={slo.id}>
          <td><span className="resource-cell-strong">{slo.name}</span><small className="resource-subtext">{slo.indicator}{slo.latency_threshold_ms ? " ≤ " + slo.latency_threshold_ms + " ms" : ""} · {slo.window_hours}h</small></td>
          <td><span className="resource-cell-strong">{serviceMap.get(slo.service_id)?.name || "Service"}</span><small className="resource-subtext">{slo.environment_id ? environmentMap.get(slo.environment_id)?.name || "Environment" : "All environments"}</small></td>
          <td>{(slo.objective * 100).toFixed(2)}%</td>
          <td>{current?.attainment === null || current?.attainment === undefined ? "No data" : (current.attainment * 100).toFixed(3) + "%"}</td>
          <td>{current?.error_budget_remaining === null || current?.error_budget_remaining === undefined ? "—" : (current.error_budget_remaining * 100).toFixed(1) + "% remaining"}<small className="resource-subtext">Burn {current?.burn_rate?.toFixed(2) || "—"}×</small></td>
          <td><ResourceStatus value={current?.status || "no_data"} /><small className="resource-subtext">{slo.last_evaluated_at ? "Updated " + formatDate(slo.last_evaluated_at) : "Awaiting first evaluation"}</small></td>
          <td><div className="resource-row-actions"><ViewAction label={slo.name} onClick={() => viewSlo(slo)} /><button className="catalog-secondary-button" disabled={!canOperate || Boolean(busy)} onClick={() => void act(slo.id, () => apiPost("/workspaces/" + workspaceId + "/slos/" + slo.id + "/evaluate"), "SLO evaluation completed.")}>Evaluate now</button></div></td>
        </tr>; })}</tbody>
      </table></div>}
      {displaySlos.length > 0 && <TablePagination firstItem={slosTable.firstItem} lastItem={slosTable.lastItem} totalItems={slosTable.filteredItems.length} page={slosTable.page} pageCount={slosTable.pageCount} onPageChange={slosTable.setPage} label="service objectives" />}
    </section>
    <section id="observability-alerts" className="resource-panel">
      <div className="resource-panel-heading"><div><h2>Alert response</h2><p>Active alerts remain open until the objective recovers; acknowledgements are audited.</p></div><button className="catalog-secondary-button" onClick={() => alerts.refresh()}>Refresh</button></div>
      <LoadingOrEmpty loading={alerts.loading} empty={Boolean(alerts.data && displayAlerts.length === 0)} label="No SLO alerts recorded for this environment" />
      {displayAlerts.length > 0 && <TableToolbar query={alertsTable.query} onQueryChange={alertsTable.setQuery} filteredCount={alertsTable.filteredItems.length} totalCount={alertsTable.totalItems} pageSize={alertsTable.pageSize} onPageSizeChange={alertsTable.setPageSize} filters={alertsTable.filters} onFilterChange={alertsTable.setFilterValue} onResetFilters={alertsTable.resetFilters} isFiltering={alertsTable.isFiltering} label="SLO alerts" />}
      {displayAlerts.length > 0 && alertsTable.filteredItems.length === 0 && <div className="resource-empty compact">No alerts match these filters.</div>}
      {alertsTable.pageItems.length > 0 && <div className="resource-table-wrap"><table className="resource-table has-actions">
        <caption className="sr-only">SLO alerts with service, state, observed attainment, start time and response actions</caption>
        <thead><tr><th scope="col">Alert</th><th scope="col">Service</th><th scope="col">State</th><th scope="col">Observed</th><th scope="col">Started</th><th scope="col">Actions</th></tr></thead>
        <tbody>{alertsTable.pageItems.map((alert) => { const slo = slos.data?.find((item) => item.id === alert.slo_id); const attainment = alert.details.attainment; return <tr key={alert.id}>
          <td><span className="resource-cell-strong">{alert.summary}</span><small className="resource-subtext">{slo?.name || "Service objective"}</small></td>
          <td>{slo ? serviceMap.get(slo.service_id)?.name || "Service" : "Service"}</td>
          <td><ResourceStatus value={alert.status} />{alert.acknowledged_at && <small className="resource-subtext">Acknowledged {formatDate(alert.acknowledged_at)}</small>}</td>
          <td>{typeof attainment === "number" ? (attainment * 100).toFixed(3) + "%" : "—"}</td>
          <td>{formatDate(alert.created_at)}</td>
          <td><div className="resource-row-actions"><ViewAction label={alert.summary} onClick={() => viewAlert(alert)} />{alert.status === "firing" && canOperate && <button className="catalog-secondary-button" disabled={Boolean(busy)} onClick={() => void act(alert.id, () => apiPost("/workspaces/" + workspaceId + "/alerts/" + alert.id + "/acknowledge"), "Alert acknowledged.")}><Check size={13} /> Acknowledge</button>}</div></td>
        </tr>; })}</tbody>
      </table></div>}
      {displayAlerts.length > 0 && <TablePagination firstItem={alertsTable.firstItem} lastItem={alertsTable.lastItem} totalItems={alertsTable.filteredItems.length} page={alertsTable.page} pageCount={alertsTable.pageCount} onPageChange={alertsTable.setPage} label="SLO alerts" />}
    </section>
    <section className="resource-panel">
      <div className="resource-panel-heading"><div><h2>Telemetry sources</h2><p>Credentials are hashed at rest and displayed only once at creation.</p></div><span>{displaySources.length} sources</span></div>
      <LoadingOrEmpty loading={sources.loading} empty={Boolean(sources.data && displaySources.length === 0)} label="No telemetry sources registered for this environment" />
      {displaySources.length > 0 && <TableToolbar query={sourcesTable.query} onQueryChange={sourcesTable.setQuery} filteredCount={sourcesTable.filteredItems.length} totalCount={sourcesTable.totalItems} pageSize={sourcesTable.pageSize} onPageSizeChange={sourcesTable.setPageSize} filters={sourcesTable.filters} onFilterChange={sourcesTable.setFilterValue} onResetFilters={sourcesTable.resetFilters} isFiltering={sourcesTable.isFiltering} label="telemetry sources" />}
      {displaySources.length > 0 && sourcesTable.filteredItems.length === 0 && <div className="resource-empty compact">No telemetry sources match these filters.</div>}
      {sourcesTable.pageItems.length > 0 && <div className="resource-table-wrap"><table className="resource-table has-actions">
        <caption className="sr-only">Telemetry sources with service, environment, token prefix, last activity, status and actions</caption>
        <thead><tr><th scope="col">Source</th><th scope="col">Service scope</th><th scope="col">Token prefix</th><th scope="col">Last activity</th><th scope="col">Status</th><th scope="col">Actions</th></tr></thead>
        <tbody>{sourcesTable.pageItems.map((source) => <tr key={source.id}>
          <td><span className="resource-cell-strong">{source.name}</span></td>
          <td>{serviceMap.get(source.service_id)?.name || "Service"}<small className="resource-subtext">{source.environment_id ? environmentMap.get(source.environment_id)?.name || "Environment" : "All environments"}</small></td>
          <td><code className="resource-mono">{source.token_prefix}</code></td>
          <td>{source.last_seen_at ? formatDate(source.last_seen_at) : "Never connected"}</td>
          <td><span className={"resource-status " + (source.enabled ? "success" : "neutral")}><span className="status-dot" />{source.enabled ? "Enabled" : "Revoked"}</span></td>
          <td><div className="resource-row-actions"><ViewAction label={source.name} onClick={() => viewSource(source)} /><label className="toggle-inline"><input type="checkbox" aria-label={"Enable telemetry source " + source.name} checked={source.enabled} disabled={!canOperate || Boolean(busy)} onChange={(event) => void act(source.id, () => apiPatch("/workspaces/" + workspaceId + "/telemetry-sources/" + source.id, { enabled: event.target.checked }), event.target.checked ? "Telemetry source enabled." : "Telemetry source revoked.")} /><span>{source.enabled ? "Enabled" : "Revoked"}</span></label></div></td>
        </tr>)}</tbody>
      </table></div>}
      {displaySources.length > 0 && <TablePagination firstItem={sourcesTable.firstItem} lastItem={sourcesTable.lastItem} totalItems={sourcesTable.filteredItems.length} page={sourcesTable.page} pageCount={sourcesTable.pageCount} onPageChange={sourcesTable.setPage} label="telemetry sources" />}
    </section>
    {selectedRecord && <RecordDrawer record={selectedRecord} onClose={() => setSelectedRecord(null)} />}
  </WorkspacePage>;
}

function Metric({ label, value, icon, tone, active, onClick }: { label: string; value: number | string; icon: React.ReactNode; tone: string; active?: boolean; onClick?: () => void }) {
  const content = <><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span></>;
  return onClick ? <button type="button" className={`metric-card metric-action ${tone} ${active ? "is-active" : ""}`} onClick={onClick} aria-pressed={active} aria-label={`Show ${label.toLowerCase()}: ${value}`}>{content}<ArrowUpRight size={14} className="kpi-action-icon" /></button> : <article className={`metric-card ${tone}`}>{content}</article>;
}

function detailValue(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}
