import { useEffect, useMemo, useState } from "react";
import { Activity, BellRing, Check, Gauge, KeyRound, Plus, Radio, ShieldCheck, X } from "lucide-react";
import { apiGet, apiPatch, apiPost, type Environment, type ServiceCatalog, type Slo, type SloAlert, type SloEvaluation, type TelemetrySource } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, useWorkspaceResource, WorkspacePage } from "../components/WorkspacePage";

export default function ObservabilityPage() {
  const { workspaceId, environmentId, canOperate } = usePlatformContext();
  const slos = useWorkspaceResource<Slo[]>(workspaceId ? `/workspaces/${workspaceId}/slos` : null);
  const alerts = useWorkspaceResource<SloAlert[]>(workspaceId ? `/workspaces/${workspaceId}/alerts?limit=200` : null);
  const sources = useWorkspaceResource<TelemetrySource[]>(workspaceId ? `/workspaces/${workspaceId}/telemetry-sources` : null);
  const services = useWorkspaceResource<ServiceCatalog>(workspaceId ? `/workspaces/${workspaceId}/services?limit=100` : null, 60_000);
  const environments = useWorkspaceResource<Environment[]>(workspaceId ? `/workspaces/${workspaceId}/environments` : null, 60_000);
  const [evaluations, setEvaluations] = useState<Record<string, SloEvaluation | null>>({});
  const [showSlo, setShowSlo] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const [sloService, setSloService] = useState("");
  const [sloEnvironment, setSloEnvironment] = useState("");
  const [sloName, setSloName] = useState("");
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
  const serviceMap = useMemo(() => new Map((services.data?.items || []).map((service) => [service.id, service])), [services.data]);
  const environmentMap = useMemo(() => new Map((environments.data || []).map((environment) => [environment.id, environment])), [environments.data]);
  const displaySlos = useMemo(() => (slos.data || []).filter((slo) => !environmentId || !slo.environment_id || slo.environment_id === environmentId), [environmentId, slos.data]);
  const displaySources = useMemo(() => (sources.data || []).filter((source) => !environmentId || !source.environment_id || source.environment_id === environmentId), [environmentId, sources.data]);

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
    setBusy(id); setError(""); setNotice("");
    try { await operation(); setNotice(message); slos.refresh(); alerts.refresh(); sources.refresh(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "The observability action could not be completed."); }
    finally { setBusy(""); }
  }

  async function createSlo(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId) return;
    const slug = sloName.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    await act("new-slo", async () => {
      await apiPost(`/workspaces/${workspaceId}/slos`, { slug, name: sloName, service_id: sloService, environment_id: sloEnvironment || undefined, indicator: sloKind, objective: Number(objective) / 100, window_hours: Number(windowHours), latency_threshold_ms: sloKind === "latency" ? Number(latency) : undefined });
      setSloName(""); setSloService(""); setSloEnvironment(""); setShowSlo(false);
    }, "Service level objective created.");
  }

  async function createSource(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId) return;
    setCreatedToken("");
    await act("new-source", async () => {
      const result = await apiPost<TelemetrySource & { token: string }>(`/workspaces/${workspaceId}/telemetry-sources`, { name: sourceName, service_id: sourceService, environment_id: sourceEnvironment || undefined });
      setCreatedToken(result.token); setSourceName(""); setSourceService(""); setSourceEnvironment(""); setShowSource(false);
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
  const breaching = displaySlos.filter((slo) => evaluations[slo.id]?.status === "breaching").length;
  const averageBudget = displaySlos.flatMap((slo) => {
    const budget = evaluations[slo.id]?.error_budget_remaining;
    return budget === null || budget === undefined ? [] : [budget];
  });
  const budgetPercent = averageBudget.length ? Math.round(averageBudget.reduce((sum, item) => sum + item, 0) / averageBudget.length * 100) : null;

  return <WorkspacePage eyebrow="RELIABILITY" title="Observability" description="Connect service telemetry, define SLOs and respond to live reliability signals." actions={<><button className="catalog-secondary-button" onClick={() => setShowSource((value) => !value)} disabled={!workspaceId || !canOperate}><Radio size={13} /> Telemetry source</button><button className="catalog-primary-button" onClick={() => setShowSlo((value) => !value)} disabled={!workspaceId || !canOperate}><Plus size={14} /> Create SLO</button></>}>
    <ResourceErrors resources={[{ label: "Service objectives", error: slos.error }, { label: "SLO alerts", error: alerts.error }, { label: "Telemetry sources", error: sources.error }, { label: "Services", error: services.error }, { label: "Environments", error: environments.error }]} />
    <div className="metric-grid metric-grid-4"><Metric label="Service objectives" value={displaySlos.length} icon={<Gauge size={16} />} tone="cyan" /><Metric label="Breaching objectives" value={breaching} icon={<Activity size={16} />} tone={breaching ? "red" : "green"} /><Metric label="Active alerts" value={activeAlerts.length} icon={<BellRing size={16} />} tone={activeAlerts.length ? "amber" : "green"} /><Metric label="Avg. error budget" value={budgetPercent === null ? "—" : `${budgetPercent}%`} icon={<ShieldCheck size={16} />} tone="violet" /></div>
    {(notice || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || notice}<button className="notice-dismiss" onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss message"><X size={14} /></button></div>}
    {createdToken && <section className="credential-panel"><div className="credential-panel-icon"><KeyRound size={16} /></div><div><strong>One-time telemetry credential</strong><p>Use this bearer token in your telemetry exporter. Store it in a secrets manager; it cannot be retrieved later.</p><code>{createdToken}</code></div><button className="catalog-secondary-button" onClick={() => void copyToken()}>{tokenCopied ? "Copied" : "Copy token"}</button><button className="icon-button" onClick={() => { setCreatedToken(""); setTokenCopied(false); }} aria-label="Dismiss credential"><X size={15} /></button></section>}
    {showSlo && <section className="settings-panel resource-form-panel"><div className="settings-panel-heading"><div className="settings-panel-icon"><Gauge size={16} /></div><div><h2>Define a service level objective</h2><p>Objectives are evaluated automatically by the worker every minute.</p></div></div>{!canOperate ? <p className="permission-note">Platform engineer or workspace administrator access is required.</p> : <form className="resource-form" onSubmit={(event) => void createSlo(event)}>
      <label>Name<input required minLength={2} value={sloName} onChange={(event) => setSloName(event.target.value)} placeholder="Checkout availability" /></label><label>Service<select required value={sloService} onChange={(event) => setSloService(event.target.value)}><option value="">Select service</option>{services.data?.items.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select></label><label>Environment scope<select value={sloEnvironment} onChange={(event) => setSloEnvironment(event.target.value)}><option value="">All environments</option>{environments.data?.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)}</select></label><label>Indicator<select value={sloKind} onChange={(event) => setSloKind(event.target.value as "availability" | "latency")}><option value="availability">Availability</option><option value="latency">Latency threshold</option></select></label><label>Objective (%)<input type="number" min="1" max="99.99" step="0.01" required value={objective} onChange={(event) => setObjective(event.target.value)} /></label><label>Window (hours)<input type="number" min="1" max="720" required value={windowHours} onChange={(event) => setWindowHours(event.target.value)} /></label>{sloKind === "latency" && <label>Latency threshold<select value={latency} onChange={(event) => setLatency(event.target.value)}>{[100, 300, 500, 1000, 2500, 5000].map((value) => <option key={value} value={value}>{value} ms</option>)}</select></label>}<button className="settings-primary-button" disabled={busy === "new-slo" || !sloName.trim() || !sloService}><Plus size={14} />Create objective</button>
    </form>}</section>}
    {showSource && <section className="settings-panel resource-form-panel"><div className="settings-panel-heading"><div className="settings-panel-icon"><Radio size={16} /></div><div><h2>Register a telemetry source</h2><p>Credentials are bound to one service and can optionally be limited to one environment.</p></div></div>{!canOperate ? <p className="permission-note">Platform engineer or workspace administrator access is required.</p> : <form className="resource-form" onSubmit={(event) => void createSource(event)}><label>Source name<input required minLength={2} maxLength={120} value={sourceName} onChange={(event) => setSourceName(event.target.value)} placeholder="checkout-otel-collector" /></label><label>Service<select required value={sourceService} onChange={(event) => setSourceService(event.target.value)}><option value="">Select service</option>{services.data?.items.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select></label><label>Environment <span className="field-optional">optional</span><select value={sourceEnvironment} onChange={(event) => setSourceEnvironment(event.target.value)}><option value="">All environments</option>{environments.data?.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)}</select></label><button className="settings-primary-button" disabled={busy === "new-source" || !sourceName.trim() || !sourceService}><KeyRound size={14} />Create credential</button></form>}</section>}
    <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Service level objectives</h2><p>Attainment, error budget and burn rate are computed from ingested service telemetry.</p></div><span className="live-indicator"><span className="status-dot" /> Evaluated by worker</span></div><LoadingOrEmpty loading={slos.loading} empty={Boolean(slos.data && displaySlos.length === 0)} label="No service level objectives defined for this environment" />
      {displaySlos.length > 0 && <div className="resource-table-wrap"><table className="resource-table"><thead><tr><th>Objective</th><th>Service scope</th><th>Target</th><th>Current attainment</th><th>Error budget</th><th>Status / action</th></tr></thead><tbody>{displaySlos.map((slo) => { const current = evaluations[slo.id]; return <tr key={slo.id}><td><span className="resource-cell-strong">{slo.name}</span><small className="resource-subtext">{slo.indicator}{slo.latency_threshold_ms ? ` ≤ ${slo.latency_threshold_ms} ms` : ""} · {slo.window_hours}h</small></td><td><span className="resource-cell-strong">{serviceMap.get(slo.service_id)?.name || "Service"}</span><small className="resource-subtext">{slo.environment_id ? environmentMap.get(slo.environment_id)?.name || "Environment" : "All environments"}</small></td><td>{(slo.objective * 100).toFixed(2)}%</td><td>{current?.attainment === null || current?.attainment === undefined ? "No data" : `${(current.attainment * 100).toFixed(3)}%`}</td><td>{current?.error_budget_remaining === null || current?.error_budget_remaining === undefined ? "—" : `${(current.error_budget_remaining * 100).toFixed(1)}% remaining`}<small className="resource-subtext">Burn {current?.burn_rate?.toFixed(2) || "—"}×</small></td><td><div className="resource-row-actions"><ResourceStatus value={current?.status || "no_data"} /><button className="catalog-secondary-button" disabled={!canOperate || busy === slo.id} onClick={() => void act(slo.id, () => apiPost(`/workspaces/${workspaceId}/slos/${slo.id}/evaluate`), "SLO evaluation completed.")}>Evaluate now</button></div><small className="resource-subtext">{slo.last_evaluated_at ? `Updated ${formatDate(slo.last_evaluated_at)}` : "Awaiting first evaluation"}</small></td></tr>; })}</tbody></table></div>}
    </section>
    <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Alert response</h2><p>Active alerts remain open until the objective recovers; acknowledgements are audited.</p></div><button className="catalog-secondary-button" onClick={() => alerts.refresh()}>Refresh</button></div><LoadingOrEmpty loading={alerts.loading} empty={Boolean(alerts.data && displayAlerts.length === 0)} label="No SLO alerts recorded for this environment" />
      {displayAlerts.length > 0 && <div className="resource-table-wrap"><table className="resource-table"><thead><tr><th>Alert</th><th>Service</th><th>State</th><th>Observed</th><th>Started</th><th>Response</th></tr></thead><tbody>{displayAlerts.map((alert) => { const slo = slos.data?.find((item) => item.id === alert.slo_id); const attainment = alert.details.attainment; return <tr key={alert.id}><td><span className="resource-cell-strong">{alert.summary}</span><small className="resource-subtext">{slo?.name || "Service objective"}</small></td><td>{slo ? serviceMap.get(slo.service_id)?.name || "Service" : "Service"}</td><td><ResourceStatus value={alert.status} /></td><td>{typeof attainment === "number" ? `${(attainment * 100).toFixed(3)}%` : "—"}</td><td>{formatDate(alert.created_at)}</td><td>{alert.status === "firing" && canOperate ? <button className="catalog-secondary-button" disabled={busy === alert.id} onClick={() => void act(alert.id, () => apiPost(`/workspaces/${workspaceId}/alerts/${alert.id}/acknowledge`), "Alert acknowledged.")}><Check size={13} /> Acknowledge</button> : alert.acknowledged_at ? formatDate(alert.acknowledged_at) : "—"}</td></tr>; })}</tbody></table></div>}
    </section>
    <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Telemetry sources</h2><p>Credentials are hashed at rest and displayed only once at creation.</p></div><span>{displaySources.length} sources</span></div><LoadingOrEmpty loading={sources.loading} empty={Boolean(sources.data && displaySources.length === 0)} label="No telemetry sources registered for this environment" />{displaySources.map((source) => <article className="evaluation-row" key={source.id}><span className="source-icon"><Radio size={14} /></span><div><strong>{source.name}</strong><small>{serviceMap.get(source.service_id)?.name || "Service"} · {source.environment_id ? environmentMap.get(source.environment_id)?.name || "Environment" : "All environments"} · Token {source.token_prefix}</small></div><span className="source-last-seen">{source.last_seen_at ? `Seen ${formatDate(source.last_seen_at)}` : "Never connected"}</span><label className="toggle-inline"><input type="checkbox" checked={source.enabled} disabled={!canOperate || busy === source.id} onChange={(event) => void act(source.id, () => apiPatch(`/workspaces/${workspaceId}/telemetry-sources/${source.id}`, { enabled: event.target.checked }), event.target.checked ? "Telemetry source enabled." : "Telemetry source revoked.")} /><span>{source.enabled ? "Enabled" : "Revoked"}</span></label></article>)}
    </section>
  </WorkspacePage>;
}

function Metric({ label, value, icon, tone }: { label: string; value: number | string; icon: React.ReactNode; tone: string }) { return <article className={`metric-card ${tone}`}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span></article>; }
