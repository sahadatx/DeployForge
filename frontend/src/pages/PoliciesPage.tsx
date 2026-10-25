import { useEffect, useMemo, useState } from "react";
import { Check, FileCheck2, Plus, ShieldCheck, X } from "lucide-react";
import { apiPatch, apiPost, type Environment, type Policy, type PolicyEvaluation, type PolicyException, type ServiceCatalog } from "../lib/api";
import { usePlatformContext } from "../components/PlatformContext";
import { formatDate, LoadingOrEmpty, ResourceErrors, ResourceStatus, useWorkspaceResource, WorkspacePage } from "../components/WorkspacePage";

const DEFAULT_RULE = [{ id: "protected-target", name: "Require protected target", description: "Require an approval when an environment is not protected.", field: "environment.protected", operator: "equals", expected: false, effect: "require_approval" }];

export default function PoliciesPage() {
  const { workspaceId, canOperate } = usePlatformContext();
  const policies = useWorkspaceResource<Policy[]>(workspaceId ? `/workspaces/${workspaceId}/policies` : null);
  const evaluations = useWorkspaceResource<PolicyEvaluation[]>(workspaceId ? `/workspaces/${workspaceId}/policy-evaluations?limit=30` : null);
  const services = useWorkspaceResource<ServiceCatalog>(workspaceId ? `/workspaces/${workspaceId}/services?limit=100` : null, 60_000);
  const environments = useWorkspaceResource<Environment[]>(workspaceId ? `/workspaces/${workspaceId}/environments` : null, 60_000);
  const [selectedPolicyId, setSelectedPolicyId] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [policyName, setPolicyName] = useState("");
  const [policySlug, setPolicySlug] = useState("");
  const [rulesJson, setRulesJson] = useState(JSON.stringify(DEFAULT_RULE, null, 2));
  const [evaluationAction, setEvaluationAction] = useState<"deployment" | "infrastructure">("deployment");
  const [serviceId, setServiceId] = useState("");
  const [environmentId, setEnvironmentId] = useState("");
  const [contextJson, setContextJson] = useState('{\n  "environment": { "type": "production", "protected": true }\n}');
  const [exceptionRule, setExceptionRule] = useState("");
  const [exceptionService, setExceptionService] = useState("");
  const [exceptionEnvironment, setExceptionEnvironment] = useState("");
  const [exceptionReason, setExceptionReason] = useState("");
  const [exceptionExpiry, setExceptionExpiry] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => { if (!selectedPolicyId && policies.data?.length) setSelectedPolicyId(policies.data[0].id); }, [policies.data, selectedPolicyId]);
  const selectedPolicy = policies.data?.find((policy) => policy.id === selectedPolicyId) || null;
  const exceptions = useWorkspaceResource<PolicyException[]>(workspaceId && selectedPolicyId ? `/workspaces/${workspaceId}/policies/${selectedPolicyId}/exceptions` : null, 20_000);
  const rules = useMemo(() => selectedPolicy?.rules || [], [selectedPolicy]);

  async function act(id: string, operation: () => Promise<unknown>, message: string) {
    setBusy(id); setError(""); setNotice("");
    try { await operation(); setNotice(message); policies.refresh(); evaluations.refresh(); exceptions.refresh(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "The policy action could not be completed."); }
    finally { setBusy(""); }
  }

  async function createPolicy(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId) return;
    try {
      const rules = JSON.parse(rulesJson);
      if (!Array.isArray(rules)) throw new Error("Rules must be a JSON array.");
      await act("new-policy", async () => {
        const created = await apiPost<Policy>(`/workspaces/${workspaceId}/policies`, { slug: policySlug, name: policyName, description: "Created from the DeployForge policy workspace.", enabled: true, enforcement_mode: "enforce", actions: ["deployment"], rules });
        setSelectedPolicyId(created.id); setShowCreate(false); setPolicyName(""); setPolicySlug("");
      }, "Versioned policy created and audited.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Policy JSON is invalid.");
    }
  }

  async function evaluate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId) return;
    let context: unknown;
    try { context = JSON.parse(contextJson); }
    catch { setError("Evaluation context must be valid JSON."); return; }
    await act("evaluate", () => apiPost(`/workspaces/${workspaceId}/policy-evaluations/evaluate`, { action: evaluationAction, service_id: serviceId || undefined, environment_id: environmentId || undefined, context }), "Policy evaluation recorded as an immutable decision.");
  }

  async function createException(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || !selectedPolicyId) return;
    await act("exception", () => apiPost(`/workspaces/${workspaceId}/policies/${selectedPolicyId}/exceptions`, { rule_id: exceptionRule, service_id: exceptionService || undefined, environment_id: exceptionEnvironment || undefined, reason: exceptionReason, expires_at: new Date(exceptionExpiry).toISOString() }), "Scoped exception request submitted for independent review.");
    setExceptionReason("");
  }

  return <WorkspacePage eyebrow="WORKSPACE" title="Policies" description="Version and evaluate guardrails, inspect immutable decisions and review scoped exceptions." actions={<button className="catalog-primary-button" onClick={() => setShowCreate((value) => !value)} disabled={!workspaceId || !canOperate}><Plus size={14} /> New policy</button>}>
    <ResourceErrors resources={[{ label: "Policies", error: policies.error }, { label: "Policy evaluations", error: evaluations.error }, { label: "Services", error: services.error }, { label: "Environments", error: environments.error }, { label: "Scoped exceptions", error: exceptions.error }]} />
    <div className="metric-grid metric-grid-3"><Metric label="Active policies" value={policies.data?.filter((policy) => policy.enabled).length || 0} icon={<ShieldCheck size={16} />} tone="cyan" /><Metric label="Approval decisions" value={evaluations.data?.filter((item) => item.requires_approval).length || 0} icon={<FileCheck2 size={16} />} tone="amber" /><Metric label="Pending exceptions" value={exceptions.data?.filter((item) => item.status === "pending").length || 0} icon={<Check size={16} />} tone="violet" /></div>
    {(notice || error) && <div className={`settings-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}>{error || notice}<button className="notice-dismiss" onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss message"><X size={14} /></button></div>}
    {showCreate && <section className="settings-panel resource-form-panel"><div className="settings-panel-heading"><div className="settings-panel-icon"><ShieldCheck size={16} /></div><div><h2>Create policy</h2><p>Rules are validated by the policy engine and stored as a versioned snapshot.</p></div></div>{!canOperate ? <p className="permission-note">Platform engineer or workspace administrator access is required to manage policies.</p> : <form className="resource-form policy-create-form" onSubmit={(event) => void createPolicy(event)}>
      <label>Policy name<input required minLength={2} value={policyName} onChange={(event) => { setPolicyName(event.target.value); setPolicySlug(event.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")); }} placeholder="Production deployment guardrails" /></label><label>Slug<input required pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" value={policySlug} onChange={(event) => setPolicySlug(event.target.value)} /></label>
      <label className="form-wide">Rules JSON<textarea rows={9} value={rulesJson} onChange={(event) => setRulesJson(event.target.value)} spellCheck={false} /></label>
      <div className="form-note">Default rule: request approval for a deployment to an unprotected environment.</div><button className="settings-primary-button" disabled={busy === "new-policy" || !policyName.trim() || !policySlug.trim()}><Plus size={14} />Create version 1</button>
    </form>}</section>}
    <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Workspace guardrails</h2><p>Each edit increments the policy version; previous evaluation snapshots remain unchanged.</p></div><span className="live-indicator"><span className="status-dot" /> Policy engine</span></div>
      <LoadingOrEmpty loading={policies.loading} empty={Boolean(policies.data && policies.data.length === 0)} label="No policy rules defined" />
      {policies.data && policies.data.length > 0 && <div className="policy-layout"><div className="policy-list">{policies.data.map((policy) => <button key={policy.id} className={`policy-list-item ${selectedPolicyId === policy.id ? "selected" : ""}`} onClick={() => setSelectedPolicyId(policy.id)}><span className="policy-list-icon"><ShieldCheck size={15} /></span><span><strong>{policy.name}</strong><small>{policy.slug} · v{policy.version}</small></span><ResourceStatus value={policy.enabled ? policy.enforcement_mode : "disabled"} /></button>)}</div>
        {selectedPolicy && <article className="policy-detail"><div className="policy-detail-heading"><div><span className="eyebrow">POLICY · VERSION {selectedPolicy.version}</span><h2>{selectedPolicy.name}</h2><p>{selectedPolicy.description || "No policy description provided."}</p></div><label className="toggle-inline"><input type="checkbox" checked={selectedPolicy.enabled} disabled={!canOperate || busy === selectedPolicy.id} onChange={(event) => void act(selectedPolicy.id, () => apiPatch(`/workspaces/${workspaceId}/policies/${selectedPolicy.id}`, { expected_version: selectedPolicy.version, enabled: event.target.checked }), event.target.checked ? "Policy enabled." : "Policy disabled.")} /><span>{selectedPolicy.enabled ? "Enabled" : "Disabled"}</span></label></div><div className="policy-meta"><span>{selectedPolicy.enforcement_mode}</span>{selectedPolicy.actions.map((action) => <span key={action}>{action}</span>)}<span>{selectedPolicy.rules.length} rules</span></div><div className="policy-rules">{rules.map((rule) => <div className="policy-rule" key={rule.id}><span className="rule-effect"><ResourceStatus value={rule.effect} /></span><span><strong>{rule.name}</strong><small>{rule.field} {rule.operator.replaceAll("_", " ")} {rule.expected === undefined ? "" : JSON.stringify(rule.expected)}</small></span><code>{rule.id}</code></div>)}</div></article>}
      </div>}
    </section>
    <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Evaluate a policy set</h2><p>Submit a typed deployment or infrastructure context to persist an auditable decision.</p></div></div>
      <form className="resource-form policy-evaluate-form" onSubmit={(event) => void evaluate(event)}><label>Action<select value={evaluationAction} onChange={(event) => setEvaluationAction(event.target.value as "deployment" | "infrastructure")}><option value="deployment">Deployment</option><option value="infrastructure">Infrastructure</option></select></label><label>Service <span className="field-optional">optional</span><select value={serviceId} onChange={(event) => setServiceId(event.target.value)}><option value="">Context only</option>{services.data?.items.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select></label><label>Environment <span className="field-optional">optional</span><select value={environmentId} onChange={(event) => setEnvironmentId(event.target.value)}><option value="">Context only</option>{environments.data?.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)}</select></label><label className="form-wide">Typed context JSON<textarea rows={5} value={contextJson} onChange={(event) => setContextJson(event.target.value)} spellCheck={false} /></label><button className="settings-primary-button" disabled={busy === "evaluate"}><ShieldCheck size={14} />Evaluate policies</button></form>
      <div className="policy-evaluation-list"><h3>Recent immutable decisions</h3><LoadingOrEmpty loading={evaluations.loading} empty={Boolean(evaluations.data && evaluations.data.length === 0)} label="No evaluations recorded" />{evaluations.data?.map((evaluation) => <article className="evaluation-row" key={evaluation.id}><div><strong>{evaluation.action} · {evaluation.subject_type} {evaluation.subject_id.slice(0, 8)}</strong><small>{formatDate(evaluation.created_at)} · Snapshot {evaluation.policy_snapshot_sha256.slice(0, 16)}</small></div><ResourceStatus value={evaluation.decision} />{evaluation.requires_approval && <span className="exception-review">Approval required</span>}</article>)}</div>
    </section>
    {selectedPolicy && <section className="resource-panel"><div className="resource-panel-heading"><div><h2>Scoped exceptions</h2><p>Exceptions expire automatically and require an independent reviewer.</p></div></div>
      {canOperate && rules.length > 0 && <form className="resource-form exception-form" onSubmit={(event) => void createException(event)}><label>Policy rule<select required value={exceptionRule} onChange={(event) => setExceptionRule(event.target.value)}><option value="">Select a rule</option>{rules.map((rule) => <option key={rule.id} value={rule.id}>{rule.name}</option>)}</select></label><label>Service scope<select value={exceptionService} onChange={(event) => setExceptionService(event.target.value)}><option value="">No service scope</option>{services.data?.items.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select></label><label>Environment scope<select value={exceptionEnvironment} onChange={(event) => setExceptionEnvironment(event.target.value)}><option value="">No environment scope</option>{environments.data?.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)}</select></label><label>Expires at<input type="datetime-local" required value={exceptionExpiry} onChange={(event) => setExceptionExpiry(event.target.value)} /></label><label className="form-wide">Reason<input required minLength={10} maxLength={2000} value={exceptionReason} onChange={(event) => setExceptionReason(event.target.value)} placeholder="Document the bounded operational reason" /></label><button className="settings-primary-button" disabled={busy === "exception" || (!exceptionService && !exceptionEnvironment)}><Plus size={14} />Request exception</button></form>}
      <LoadingOrEmpty loading={exceptions.loading} empty={Boolean(exceptions.data && exceptions.data.length === 0)} label="No exceptions for this policy" />
      {exceptions.data?.map((exception) => <article className="evaluation-row" key={exception.id}><div><strong>{exception.rule_id} · {exception.reason}</strong><small>Expires {formatDate(exception.expires_at)} · Requested {formatDate(exception.created_at)}</small></div><ResourceStatus value={exception.status} />{exception.status === "pending" && canOperate && <div className="resource-row-actions"><button className="row-action approve" disabled={busy === exception.id} onClick={() => void act(exception.id, () => apiPost(`/workspaces/${workspaceId}/policy-exceptions/${exception.id}/decision`, { decision: "approve", comment: "Reviewed in DeployForge." }), "Policy exception approved.")} title="Approve exception"><Check size={14} /></button><button className="row-action reject" disabled={busy === exception.id} onClick={() => void act(exception.id, () => apiPost(`/workspaces/${workspaceId}/policy-exceptions/${exception.id}/decision`, { decision: "reject", comment: "Rejected in DeployForge." }), "Policy exception rejected.")} title="Reject exception"><X size={14} /></button></div>}{["pending", "approved"].includes(exception.status) && canOperate && <button className="row-action reject" disabled={busy === exception.id} onClick={() => void act(exception.id, () => apiPost(`/workspaces/${workspaceId}/policy-exceptions/${exception.id}/revoke`), "Policy exception revoked.")} title="Revoke exception"><X size={14} /></button>}</article>)}
    </section>}
  </WorkspacePage>;
}

function Metric({ label, value, icon, tone }: { label: string; value: number; icon: React.ReactNode; tone: string }) { return <article className={`metric-card ${tone}`}><span className="metric-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong></span></article>; }
