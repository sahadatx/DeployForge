import { useEffect, useMemo, useState } from "react";
import { ArrowUpRight, Check, Clock3, Cloud, LockKeyhole, Plus, Shield, UnlockKeyhole, X } from "lucide-react";
import { apiDelete, apiPatch, apiPost, type Deployment, type Environment } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { FieldLabel, FormDrawer } from "../components/FormDrawer";
import { slugFromName } from "../lib/forms";
import { RecordDrawer, ViewAction } from "../components/RecordDrawer";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, TablePagination, TableToolbar, useTableView, useWorkspaceResource, WorkspacePage } from "../components/WorkspacePage";

export default function EnvironmentsPage() {
  const { workspaceId, environmentId, canOperate, isPlatformAdmin } = usePlatformContext();
  const environments = useWorkspaceResource<Environment[]>(workspaceId ? `/workspaces/${workspaceId}/environments` : null);
  const deployments = useWorkspaceResource<Deployment[]>(workspaceId ? `/workspaces/${workspaceId}/deployments?limit=100` : null, 20_000, "array");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [kind, setKind] = useState<Environment["environment_type"]>("development");
  const [cluster, setCluster] = useState("");
  const [namespace, setNamespace] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [selectedEnvironmentId, setSelectedEnvironmentId] = useState("");
  const [freezeEnvironmentId, setFreezeEnvironmentId] = useState("");
  const [freezeStartsAt, setFreezeStartsAt] = useState("");
  const [freezeEndsAt, setFreezeEndsAt] = useState("");
  const [freezeReason, setFreezeReason] = useState("");
  const deploymentsByEnvironment = useMemo(() => {
    const result = new Map<string, number>();
    for (const deployment of deployments.data || []) result.set(deployment.environment_id, (result.get(deployment.environment_id) || 0) + 1);
    return result;
  }, [deployments.data]);
  const environmentsTable = useTableView(environments.data || [], (environment) => `${environment.name} ${environment.slug} ${environment.environment_type} ${environment.cluster_name || ""} ${environment.namespace || ""} ${environment.protected ? "protected" : "unprotected"}`, {
    resetKey: `${workspaceId}:${environmentId}`,
    filters: [
      { id: "type", label: "Type", getValue: (environment) => environment.environment_type, options: [{ value: "development", label: "Development" }, { value: "staging", label: "Staging" }, { value: "production", label: "Production" }, { value: "preview", label: "Preview" }] },
      { id: "protection", label: "Protection", getValue: (environment) => environment.protected ? "protected" : "unprotected", options: [{ value: "protected", label: "Protected" }, { value: "unprotected", label: "Unprotected" }] },
      { id: "destroy", label: "Destroy plans", getValue: (environment) => environment.allow_destroy ? "allowed" : "blocked", options: [{ value: "allowed", label: "Allowed" }, { value: "blocked", label: "Blocked" }] },
    ],
  });
  const selectedEnvironment = environments.data?.find((environment) => environment.id === selectedEnvironmentId);
  const freezeEnvironment = environments.data?.find((environment) => environment.id === freezeEnvironmentId);
  const environmentFilters = Object.fromEntries(environmentsTable.filters.map((filter) => [filter.id, filter.value]));

  useEffect(() => { setSelectedEnvironmentId(""); }, [workspaceId, environmentId]);
  useEffect(() => { setCreating(false); resetEnvironmentForm(); }, [workspaceId]);

  function filterEnvironments(id?: string, value = "") {
    environmentsTable.resetFilters();
    if (id) environmentsTable.setFilterValue(id, value);
  }

  function resetEnvironmentForm() {
    setName(""); setSlug(""); setSlugEdited(false); setKind("development"); setCluster(""); setNamespace(""); setError("");
  }

  async function createEnvironment(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await apiPost(`/workspaces/${workspaceId}/environments`, {
        slug: slug.trim(),
        name: name.trim(),
        environment_type: kind,
        cluster_name: cluster.trim() || undefined,
        namespace: namespace.trim() || undefined,
      });
      resetEnvironmentForm(); setCreating(false);
      setNotice(`${name} environment created.`);
      environments.refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The environment could not be created.");
    } finally { setBusy(false); }
  }

  async function updateEnvironment(environment: Environment, patch: Partial<Environment>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await apiPatch(`/workspaces/${workspaceId}/environments/${environment.id}`, patch);
      setNotice(`${environment.name} settings updated.`);
      environments.refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The environment settings could not be updated.");
    } finally { setBusy(false); }
  }

  function openFreezeForm(environment: Environment) {
    setFreezeEnvironmentId(environment.id);
    setFreezeStartsAt(environment.deployment_freeze_start_at ? toLocalDateTimeInput(environment.deployment_freeze_start_at) : "");
    setFreezeEndsAt(environment.deployment_freeze_until ? toLocalDateTimeInput(environment.deployment_freeze_until) : "");
    setFreezeReason(environment.deployment_freeze_reason || "");
    setError("");
  }

  function resetFreezeForm() {
    setFreezeEnvironmentId(""); setFreezeStartsAt(""); setFreezeEndsAt(""); setFreezeReason("");
  }

  async function saveFreeze(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || !freezeEnvironment || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await apiPost(`/workspaces/${workspaceId}/environments/${freezeEnvironment.id}/deployment-freeze`, {
        starts_at: new Date(freezeStartsAt).toISOString(),
        ends_at: new Date(freezeEndsAt).toISOString(),
        reason: freezeReason.trim(),
      });
      setNotice(`Deployment freeze scheduled for ${freezeEnvironment.name}.`);
      resetFreezeForm();
      environments.refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The deployment freeze could not be saved.");
    } finally { setBusy(false); }
  }

  async function clearFreeze(environment: Environment) {
    if (!workspaceId || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await apiDelete(`/workspaces/${workspaceId}/environments/${environment.id}/deployment-freeze`);
      setNotice(`Deployment freeze canceled for ${environment.name}.`);
      environments.refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The deployment freeze could not be canceled.");
    } finally { setBusy(false); }
  }

  const protectedCount = environments.data?.filter((environment) => environment.protected).length || 0;
  const productionCount = environments.data?.filter((environment) => environment.environment_type === "production").length || 0;

  return <WorkspacePage eyebrow="WORKSPACE" title="Environments" description="Manage deployment boundaries, protection controls and workspace quotas." actions={<button className="catalog-primary-button" onClick={() => { resetEnvironmentForm(); setCreating(true); }} disabled={!workspaceId || !canOperate || busy}><Plus size={14} /> New environment</button>}>
    <ResourceErrors resources={[{ label: "Environments", error: environments.error }, { label: "Deployments", error: deployments.error }]} />
    <div className="metric-grid metric-grid-3"><Metric label="Environments" value={environments.data?.length || 0} icon={<Cloud size={16} />} tone="cyan" active={!environmentsTable.hasActiveFilters} onClick={() => filterEnvironments()} /><Metric label="Protected" value={protectedCount} icon={<LockKeyhole size={16} />} tone="green" active={environmentFilters.protection === "protected"} onClick={() => filterEnvironments("protection", "protected")} /><Metric label="Production" value={productionCount} icon={<Shield size={16} />} tone="violet" active={environmentFilters.type === "production"} onClick={() => filterEnvironments("type", "production")} /></div>
    {(notice || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || notice}<button className="notice-dismiss" onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss message"><X size={14} /></button></div>}
    {creating && <FormDrawer title="Register an environment" description="Production environments are protected by default. Destructive production plans are disabled." dirty={Boolean(name || slug || cluster || namespace || kind !== "development")} onClose={() => { resetEnvironmentForm(); setCreating(false); }} onReset={resetEnvironmentForm} busy={busy} error={error} actions={canOperate && <button form="create-environment-form" type="submit" className="settings-primary-button" disabled={busy || !name.trim()}><Plus size={14} />{busy ? "Creating…" : "Create environment"}</button>}>{!canOperate ? <p className="permission-note">Platform engineer or workspace administrator access is required to create environments.</p> : <form id="create-environment-form" className="resource-form" onSubmit={(event) => void createEnvironment(event)}>
      <label><FieldLabel required>Name</FieldLabel><input required minLength={2} maxLength={120} value={name} onChange={(event) => { setName(event.target.value); if (!slugEdited) setSlug(slugFromName(event.target.value, 80)); }} placeholder="Staging" /></label>
      <label><FieldLabel required>Environment slug</FieldLabel><input required maxLength={80} pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" value={slug} onChange={(event) => { setSlug(event.target.value); setSlugEdited(true); }} placeholder="staging" aria-describedby="environment-slug-hint" /></label>
      <p id="environment-slug-hint" className="form-note form-wide">Use lowercase letters, numbers and hyphens for the environment identifier. Review the suggestion before creating it.</p>
      <label><FieldLabel required>Environment type</FieldLabel><select required value={kind} onChange={(event) => setKind(event.target.value as Environment["environment_type"])}><option value="development">Development</option><option value="staging">Staging</option><option value="production">Production</option><option value="preview">Preview</option></select></label>
      <label><FieldLabel optional>Cluster name</FieldLabel><input value={cluster} onChange={(event) => setCluster(event.target.value)} placeholder="eks-prod-us-east-1" maxLength={180} /></label>
      <label><FieldLabel optional>Default namespace</FieldLabel><input value={namespace} onChange={(event) => setNamespace(event.target.value)} placeholder="platform" maxLength={63} pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" /></label>
    </form>}</FormDrawer>}
    <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Environment boundaries</h2><p>Protection and quota settings are enforced by the backend at operation time.</p></div><span className="live-indicator"><span className="status-dot" /> Updated {environments.data?.[0] ? formatDate(environments.data[0].created_at) : "live"}</span></div>
      <LoadingOrEmpty loading={environments.loading} empty={Boolean(environments.data && environments.data.length === 0)} label="No environments configured" />
      {environments.data && environments.data.length > 0 && <TableToolbar query={environmentsTable.query} onQueryChange={environmentsTable.setQuery} filteredCount={environmentsTable.filteredItems.length} totalCount={environmentsTable.totalItems} pageSize={environmentsTable.pageSize} onPageSizeChange={environmentsTable.setPageSize} filters={environmentsTable.filters} onFilterChange={environmentsTable.setFilterValue} onResetFilters={environmentsTable.resetFilters} isFiltering={environmentsTable.isFiltering} label="environments" />}
      {environments.data && environments.data.length > 0 && environmentsTable.filteredItems.length === 0 && <div className="resource-empty compact">No environments match this filter.</div>}
      {environmentsTable.pageItems.length > 0 && <><div className="environment-grid">{environmentsTable.pageItems.map((environment) => <article className="environment-card" key={environment.id}>
        <div className="environment-card-top"><span className={`environment-symbol env-${environment.environment_type}`}><Cloud size={17} /></span><ResourceStatus value={environment.environment_type} /><button className="catalog-secondary-button environment-lock" disabled={!canOperate || busy || (environment.environment_type === "production" && environment.protected && !isPlatformAdmin)} onClick={() => void updateEnvironment(environment, { protected: !environment.protected })} title={environment.protected && environment.environment_type === "production" && !isPlatformAdmin ? "Only a platform administrator can remove production protection" : environment.protected ? "Remove environment protection" : "Enable environment protection"}>{environment.protected ? <LockKeyhole size={13} /> : <UnlockKeyhole size={13} />}{environment.protected ? "Protected" : "Unprotected"}</button></div>
        <h2>{environment.name}</h2><p className="environment-slug">{environment.slug}</p>
        {environment.deployment_freeze_start_at && environment.deployment_freeze_until && <div className="environment-freeze-status"><Clock3 size={13} /><span><strong>Freeze scheduled</strong><small>{formatDate(environment.deployment_freeze_start_at)} → {formatDate(environment.deployment_freeze_until)}</small></span></div>}
        <div className="environment-facts"><span>Cluster</span><strong>{environment.cluster_name || "Not bound"}</strong><span>Namespace</span><strong>{environment.namespace || "Default"}</strong><span>Deployments</span><strong>{deploymentsByEnvironment.get(environment.id) || 0}</strong><span>Created</span><strong>{formatDate(environment.created_at)}</strong></div>
        <div className="environment-controls"><label className="toggle-row"><span><strong>Allow destroy plans</strong><small>{environment.environment_type === "production" ? "Platform administrator only" : "Infrastructure workflow guard"}</small></span><input type="checkbox" checked={environment.allow_destroy} disabled={!canOperate || busy || (environment.environment_type === "production" && !isPlatformAdmin)} onChange={(event) => { if (event.target.checked && !window.confirm(`Allow destructive infrastructure plans in ${environment.name}?`)) return; void updateEnvironment(environment, { allow_destroy: event.target.checked }); }} /><span className="toggle-track" /></label>
        <div className="quota-summary"><span>CPU {formatQuota(environment.quota_cpu_millis, "m")}</span><span>Memory {formatQuota(environment.quota_memory_mib, "MiB")}</span><span>Pods {environment.quota_pods ?? "—"}</span></div></div>
        <div className="resource-row-actions environment-card-actions"><ViewAction label={`${environment.name} environment`} onClick={() => setSelectedEnvironmentId(environment.id)} />{canOperate && <button type="button" className="catalog-secondary-button" disabled={busy} onClick={() => openFreezeForm(environment)}><Clock3 size={13} />{environment.deployment_freeze_start_at ? "Edit freeze" : "Schedule freeze"}</button>}{canOperate && environment.deployment_freeze_start_at && <button type="button" className="catalog-secondary-button" disabled={busy} onClick={() => void clearFreeze(environment)}><X size={13} />Cancel freeze</button>}</div>
      </article>)}</div></>}
      {Boolean(environments.data?.length) && <TablePagination firstItem={environmentsTable.firstItem} lastItem={environmentsTable.lastItem} totalItems={environmentsTable.filteredItems.length} page={environmentsTable.page} pageCount={environmentsTable.pageCount} onPageChange={environmentsTable.setPage} label="environments" />}
      <div className="resource-footnote"><Check size={13} /> Environment scope is shared by deployments, GitOps targets and infrastructure plans.</div>
    </section>
    {selectedEnvironment && <RecordDrawer onClose={() => setSelectedEnvironmentId("")} record={{ title: selectedEnvironment.name, eyebrow: "ENVIRONMENT DETAILS", description: selectedEnvironment.slug, sections: [
      { title: "Deployment boundary", fields: [{ label: "Type", value: selectedEnvironment.environment_type }, { label: "Cluster", value: selectedEnvironment.cluster_name }, { label: "Namespace", value: selectedEnvironment.namespace }, { label: "Created", value: formatDate(selectedEnvironment.created_at) }, { label: "Environment ID", value: selectedEnvironment.id, mono: true }] },
      { title: "Protection controls", fields: [{ label: "Protection", value: selectedEnvironment.protected ? "Protected" : "Unprotected" }, { label: "Destroy plans", value: selectedEnvironment.allow_destroy ? "Allowed" : "Blocked" }] },
      { title: "Change management", fields: [{ label: "Freeze starts", value: selectedEnvironment.deployment_freeze_start_at ? formatDate(selectedEnvironment.deployment_freeze_start_at) : null }, { label: "Freeze ends", value: selectedEnvironment.deployment_freeze_until ? formatDate(selectedEnvironment.deployment_freeze_until) : null }, { label: "Reason", value: selectedEnvironment.deployment_freeze_reason }] },
      { title: "Resource quotas", fields: [{ label: "CPU", value: formatQuota(selectedEnvironment.quota_cpu_millis, "m") }, { label: "Memory", value: formatQuota(selectedEnvironment.quota_memory_mib, "MiB") }, { label: "Pods", value: selectedEnvironment.quota_pods }, { label: "Persistent volume claims", value: selectedEnvironment.quota_persistent_volume_claims }, { label: "Services", value: selectedEnvironment.quota_services }] },
    ] }} />}
    {freezeEnvironment && <FormDrawer title={`${freezeEnvironment.deployment_freeze_start_at ? "Update" : "Schedule"} deployment freeze`} description={`New releases and Argo CD syncs for ${freezeEnvironment.name} pause during this window.`} dirty={Boolean(freezeStartsAt || freezeEndsAt || freezeReason)} onClose={resetFreezeForm} onReset={resetFreezeForm} busy={busy} error={error} actions={<button form="environment-freeze-form" type="submit" className="settings-primary-button" disabled={busy || !freezeStartsAt || !freezeEndsAt || freezeReason.trim().length < 10}><Clock3 size={14} />{busy ? "Saving…" : "Save freeze window"}</button>}><form id="environment-freeze-form" className="resource-form" onSubmit={(event) => void saveFreeze(event)}><label><FieldLabel required>Starts at</FieldLabel><input type="datetime-local" required value={freezeStartsAt} onChange={(event) => setFreezeStartsAt(event.target.value)} /></label><label><FieldLabel required>Ends at</FieldLabel><input type="datetime-local" required value={freezeEndsAt} onChange={(event) => setFreezeEndsAt(event.target.value)} /></label><label className="form-wide"><FieldLabel required>Change-freeze reason</FieldLabel><textarea required minLength={10} maxLength={2000} value={freezeReason} onChange={(event) => setFreezeReason(event.target.value)} placeholder="Describe the change freeze or maintenance window" /></label></form><p className="form-note">Times use your browser's time zone and are stored in UTC. Freeze windows are capped at 30 days. An already-running Argo CD operation is allowed to finish; no new sync starts during the window.</p></FormDrawer>}
  </WorkspacePage>;
}

function toLocalDateTimeInput(value: string) { const date = new Date(value); date.setMinutes(date.getMinutes() - date.getTimezoneOffset()); return date.toISOString().slice(0, 16); }
function formatQuota(value: number | null, unit: string) { return value === null ? "—" : `${value.toLocaleString()} ${unit}`; }
function Metric({ label, value, icon, tone, active, onClick }: { label: string; value: number; icon: React.ReactNode; tone: string; active: boolean; onClick: () => void }) { return <button type="button" className={`metric-card metric-action ${tone} ${active ? "is-active" : ""}`} aria-pressed={active} onClick={onClick} aria-label={`Show ${label.toLowerCase()}: ${value}`}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span><ArrowUpRight size={14} className="kpi-action-icon" /></button>; }
