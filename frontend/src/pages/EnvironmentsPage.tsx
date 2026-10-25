import { useMemo, useState } from "react";
import { Check, Cloud, LockKeyhole, Plus, Shield, UnlockKeyhole, X } from "lucide-react";
import { apiPatch, apiPost, type Deployment, type Environment } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, useWorkspaceResource, WorkspacePage } from "../components/WorkspacePage";

export default function EnvironmentsPage() {
  const { workspaceId, canOperate, isPlatformAdmin } = usePlatformContext();
  const environments = useWorkspaceResource<Environment[]>(workspaceId ? `/workspaces/${workspaceId}/environments` : null);
  const deployments = useWorkspaceResource<Deployment[]>(workspaceId ? `/workspaces/${workspaceId}/deployments?limit=100` : null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Environment["environment_type"]>("development");
  const [cluster, setCluster] = useState("");
  const [namespace, setNamespace] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const deploymentsByEnvironment = useMemo(() => {
    const result = new Map<string, number>();
    for (const deployment of deployments.data || []) result.set(deployment.environment_id, (result.get(deployment.environment_id) || 0) + 1);
    return result;
  }, [deployments.data]);

  async function createEnvironment(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId) return;
    setBusy(true); setError(""); setNotice("");
    const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    try {
      await apiPost(`/workspaces/${workspaceId}/environments`, {
        slug,
        name,
        environment_type: kind,
        cluster_name: cluster || undefined,
        namespace: namespace || undefined,
      });
      setName(""); setCluster(""); setNamespace(""); setCreating(false);
      setNotice(`${name} environment created.`);
      environments.refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The environment could not be created.");
    } finally { setBusy(false); }
  }

  async function updateEnvironment(environment: Environment, patch: Partial<Environment>) {
    setBusy(true); setError(""); setNotice("");
    try {
      await apiPatch(`/workspaces/${workspaceId}/environments/${environment.id}`, patch);
      setNotice(`${environment.name} settings updated.`);
      environments.refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The environment settings could not be updated.");
    } finally { setBusy(false); }
  }

  const protectedCount = environments.data?.filter((environment) => environment.protected).length || 0;
  const productionCount = environments.data?.filter((environment) => environment.environment_type === "production").length || 0;

  return <WorkspacePage eyebrow="WORKSPACE" title="Environments" description="Manage deployment boundaries, protection controls and workspace quotas." actions={<button className="catalog-primary-button" onClick={() => setCreating((value) => !value)} disabled={!workspaceId || !canOperate}><Plus size={14} /> New environment</button>}>
    <ResourceErrors resources={[{ label: "Environments", error: environments.error }, { label: "Deployments", error: deployments.error }]} />
    <div className="metric-grid metric-grid-3"><Metric label="Environments" value={environments.data?.length || 0} icon={<Cloud size={16} />} tone="cyan" /><Metric label="Protected" value={protectedCount} icon={<LockKeyhole size={16} />} tone="green" /><Metric label="Production" value={productionCount} icon={<Shield size={16} />} tone="violet" /></div>
    {(notice || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || notice}<button className="notice-dismiss" onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss message"><X size={14} /></button></div>}
    {creating && <section className="settings-panel resource-form-panel"><div className="settings-panel-heading"><div className="settings-panel-icon"><Cloud size={16} /></div><div><h2>Register an environment</h2><p>Production environments are protected by default. Destructive production plans are disabled.</p></div></div>{!canOperate ? <p className="permission-note">Platform engineer or workspace administrator access is required to create environments.</p> : <form className="resource-form" onSubmit={(event) => void createEnvironment(event)}>
      <label>Name<input required minLength={2} maxLength={120} value={name} onChange={(event) => setName(event.target.value)} placeholder="Staging" /></label>
      <label>Environment type<select value={kind} onChange={(event) => setKind(event.target.value as Environment["environment_type"])}><option value="development">Development</option><option value="staging">Staging</option><option value="production">Production</option><option value="preview">Preview</option></select></label>
      <label>Cluster name <span className="field-optional">optional</span><input value={cluster} onChange={(event) => setCluster(event.target.value)} placeholder="eks-prod-us-east-1" maxLength={180} /></label>
      <label>Default namespace <span className="field-optional">optional</span><input value={namespace} onChange={(event) => setNamespace(event.target.value)} placeholder="platform" maxLength={63} pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" /></label>
      <button className="settings-primary-button" disabled={busy || !name.trim()}><Plus size={14} />{busy ? "Creating…" : "Create environment"}</button>
    </form>}</section>}
    <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Environment boundaries</h2><p>Protection and quota settings are enforced by the backend at operation time.</p></div><span className="live-indicator"><span className="status-dot" /> Updated {environments.data?.[0] ? formatDate(environments.data[0].created_at) : "live"}</span></div>
      <LoadingOrEmpty loading={environments.loading} empty={Boolean(environments.data && environments.data.length === 0)} label="No environments configured" />
      {environments.data && environments.data.length > 0 && <div className="environment-grid">{environments.data.map((environment) => <article className="environment-card" key={environment.id}>
        <div className="environment-card-top"><span className={`environment-symbol env-${environment.environment_type}`}><Cloud size={17} /></span><ResourceStatus value={environment.environment_type} /><button className="catalog-secondary-button environment-lock" disabled={!canOperate || busy || (environment.environment_type === "production" && environment.protected && !isPlatformAdmin)} onClick={() => void updateEnvironment(environment, { protected: !environment.protected })} title={environment.protected && environment.environment_type === "production" && !isPlatformAdmin ? "Only a platform administrator can remove production protection" : environment.protected ? "Remove environment protection" : "Enable environment protection"}>{environment.protected ? <LockKeyhole size={13} /> : <UnlockKeyhole size={13} />}{environment.protected ? "Protected" : "Unprotected"}</button></div>
        <h2>{environment.name}</h2><p className="environment-slug">{environment.slug}</p>
        <div className="environment-facts"><span>Cluster</span><strong>{environment.cluster_name || "Not bound"}</strong><span>Namespace</span><strong>{environment.namespace || "Default"}</strong><span>Deployments</span><strong>{deploymentsByEnvironment.get(environment.id) || 0}</strong><span>Created</span><strong>{formatDate(environment.created_at)}</strong></div>
        <div className="environment-controls"><label className="toggle-row"><span><strong>Allow destroy plans</strong><small>{environment.environment_type === "production" ? "Platform administrator only" : "Infrastructure workflow guard"}</small></span><input type="checkbox" checked={environment.allow_destroy} disabled={!canOperate || busy || (environment.environment_type === "production" && !isPlatformAdmin)} onChange={(event) => { if (event.target.checked && !window.confirm(`Allow destructive infrastructure plans in ${environment.name}?`)) return; void updateEnvironment(environment, { allow_destroy: event.target.checked }); }} /><span className="toggle-track" /></label>
        <div className="quota-summary"><span>CPU {formatQuota(environment.quota_cpu_millis, "m")}</span><span>Memory {formatQuota(environment.quota_memory_mib, "MiB")}</span><span>Pods {environment.quota_pods ?? "—"}</span></div></div>
      </article>)}</div>}
      <div className="resource-footnote"><Check size={13} /> Environment scope is shared by deployments, GitOps targets and infrastructure plans.</div>
    </section>
  </WorkspacePage>;
}

function formatQuota(value: number | null, unit: string) { return value === null ? "—" : `${value.toLocaleString()} ${unit}`; }
function Metric({ label, value, icon, tone }: { label: string; value: number; icon: React.ReactNode; tone: string }) { return <article className={`metric-card ${tone}`}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span></article>; }
