import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ArrowDownRight,
  ArrowUpRight,
  Boxes,
  Braces,
  Check,
  ChevronDown,
  CircleAlert,
  ExternalLink,
  GitBranch,
  Layers3,
  LoaderCircle,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Workflow,
  X,
} from "lucide-react";
import { PageFrame } from "../components/PageFrame";
import {
  apiGet,
  apiPatch,
  apiPost,
  type AuthSession,
  type GoldenPath,
  type ServiceCatalog,
  type ServiceCatalogSummary,
  type ServiceCriticality,
  type ServiceLifecycle,
  type ServiceRecord,
  type ServiceTeam,
} from "../lib/api";

type ServiceDraft = {
  slug: string;
  name: string;
  description: string;
  system: string;
  owner_team_id: string;
  repository_url: string;
  documentation_url: string;
  lifecycle: ServiceLifecycle;
  criticality: ServiceCriticality;
  tags: string;
  metadata: Record<string, string>;
};

const emptyDraft: ServiceDraft = {
  slug: "",
  name: "",
  description: "",
  system: "",
  owner_team_id: "",
  repository_url: "",
  documentation_url: "",
  lifecycle: "experimental",
  criticality: "tier_2",
  tags: "",
  metadata: {},
};

function slugFromName(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 100);
}

function displayDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}

function lifecycleLabel(value: ServiceLifecycle) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function criticalityLabel(value: ServiceCriticality) {
  return value.replace("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function GoldenPathIcon({ icon, size = 20 }: { icon: string; size?: number }) {
  if (icon === "panels-top-left") return <Layers3 size={size} />;
  if (icon === "workflow") return <Workflow size={size} />;
  return <Braces size={size} />;
}

export default function ServicesPage() {
  const [workspaceId, setWorkspaceId] = useState(() => localStorage.getItem("deployforge.workspace") || "");
  const [searchParams, setSearchParams] = useSearchParams();
  const [session, setSession] = useState<AuthSession | null>(null);
  const [catalog, setCatalog] = useState<ServiceCatalog | null>(null);
  const [summary, setSummary] = useState<ServiceCatalogSummary | null>(null);
  const [teams, setTeams] = useState<ServiceTeam[]>([]);
  const [goldenPaths, setGoldenPaths] = useState<GoldenPath[]>([]);
  const [tab, setTab] = useState<"services" | "golden-paths">("services");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [lifecycle, setLifecycle] = useState("all");
  const [ownerTeamId, setOwnerTeamId] = useState("all");
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [teamOpen, setTeamOpen] = useState(false);
  const [selectedService, setSelectedService] = useState<ServiceRecord | null>(null);
  const [editingService, setEditingService] = useState<ServiceRecord | null>(null);
  const [selectedPath, setSelectedPath] = useState<GoldenPath | null>(null);
  const [draft, setDraft] = useState<ServiceDraft>(emptyDraft);
  const requestSequence = useRef(0);

  const selectedRole = session?.memberships.find((membership) => membership.workspace_id === workspaceId)?.role;
  const canWrite = Boolean(session?.user.is_platform_admin || selectedRole === "workspace_admin" || selectedRole === "platform_engineer");

  useEffect(() => {
    const onWorkspaceChanged = (event: Event) => {
      setWorkspaceId((event as CustomEvent<string>).detail || "");
      setSelectedService(null);
      setFormOpen(false);
      setTeamOpen(false);
    };
    window.addEventListener("deployforge:workspace-changed", onWorkspaceChanged);
    return () => window.removeEventListener("deployforge:workspace-changed", onWorkspaceChanged);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => { setPage(0); }, [search, lifecycle, ownerTeamId, workspaceId]);

  useEffect(() => {
    const controller = new AbortController();
    apiGet<AuthSession>("/auth/me", controller.signal).then(setSession).catch(() => undefined);
    return () => controller.abort();
  }, []);

  const loadCatalog = useCallback(async () => {
    const requestId = ++requestSequence.current;
    if (!workspaceId) {
      setCatalog({ items: [], total: 0, limit: 50, offset: 0 });
      setSummary(null);
      setTeams([]);
      setGoldenPaths([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: "20", offset: String(page * 20) });
      if (search) params.set("search", search);
      if (lifecycle !== "all") params.set("lifecycle", lifecycle);
      if (ownerTeamId !== "all") params.set("owner_team_id", ownerTeamId);
      const [services, serviceSummary, availableTeams, paths] = await Promise.all([
        apiGet<ServiceCatalog>(`/workspaces/${workspaceId}/services?${params.toString()}`),
        apiGet<ServiceCatalogSummary>(`/workspaces/${workspaceId}/services/summary`),
        apiGet<ServiceTeam[]>(`/workspaces/${workspaceId}/teams`),
        apiGet<GoldenPath[]>(`/workspaces/${workspaceId}/golden-paths`),
      ]);
      if (requestId !== requestSequence.current) return;
      setCatalog(services);
      setSummary(serviceSummary);
      setTeams(availableTeams);
      setGoldenPaths(paths);
    } catch (caught) {
      if (requestId !== requestSequence.current) return;
      setError(caught instanceof Error ? caught.message : "Could not load the service catalog.");
    } finally {
      if (requestId === requestSequence.current) setLoading(false);
    }
  }, [workspaceId, search, lifecycle, ownerTeamId, page]);

  useEffect(() => {
    void loadCatalog();
    return () => { requestSequence.current += 1; };
  }, [loadCatalog]);

  useEffect(() => {
    const serviceId = searchParams.get("service");
    if (!workspaceId || !serviceId) return;
    const controller = new AbortController();
    apiGet<ServiceRecord>(`/workspaces/${workspaceId}/services/${serviceId}`, controller.signal)
      .then(setSelectedService)
      .catch((caught: unknown) => {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) {
          setError(caught instanceof Error ? caught.message : "Could not open this service.");
        }
      })
      .finally(() => setSearchParams({}, { replace: true }));
    return () => controller.abort();
  }, [workspaceId, searchParams, setSearchParams]);

  const services = catalog?.items ?? [];
  function openCreate(path: GoldenPath | null = null) {
    setEditingService(null);
    setSelectedPath(path);
    setDraft({
      ...emptyDraft,
      ...(path ? {
        name: "",
        lifecycle: "experimental" as const,
        metadata: Object.fromEntries(Object.entries(path.defaults).map(([key, value]) => [key, String(value)])),
      } : {}),
      owner_team_id: teams[0]?.id || "",
    });
    setError("");
    setNotice("");
    setFormOpen(true);
  }

  function openEdit(service: ServiceRecord) {
    setSelectedService(null);
    setEditingService(service);
    setSelectedPath(null);
    setDraft({
      slug: service.slug,
      name: service.name,
      description: service.description || "",
      system: service.system || "",
      owner_team_id: service.owner_team_id || "",
      repository_url: service.repository_url || "",
      documentation_url: service.documentation_url || "",
      lifecycle: service.lifecycle,
      criticality: service.criticality,
      tags: service.tags.join(", "),
      metadata: Object.fromEntries(Object.entries(service.metadata).map(([key, value]) => [key, String(value)])),
    });
    setError("");
    setFormOpen(true);
  }

  function updateDraft<K extends keyof ServiceDraft>(key: K, value: ServiceDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function submitService(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId) return;
    setSaving(true);
    setError("");
    setNotice("");
    const payload = {
      ...(editingService ? {} : { slug: draft.slug || slugFromName(draft.name) }),
      name: draft.name.trim(),
      description: draft.description.trim() || null,
      system: draft.system.trim() || null,
      owner_team_id: draft.owner_team_id,
      repository_url: draft.repository_url.trim() || null,
      documentation_url: draft.documentation_url.trim() || null,
      lifecycle: draft.lifecycle,
      criticality: draft.criticality,
      tags: draft.tags.split(",").map((tag) => tag.trim()).filter(Boolean),
      metadata: Object.fromEntries(Object.entries(draft.metadata).filter(([, value]) => value.trim()).map(([key, value]) => [key, value.trim()])),
    };
    try {
      const result = editingService
        ? await apiPatch<ServiceRecord>(`/workspaces/${workspaceId}/services/${editingService.id}`, payload)
        : selectedPath
          ? await apiPost<ServiceRecord>(`/workspaces/${workspaceId}/golden-paths/${selectedPath.slug}/instantiate`, payload)
          : await apiPost<ServiceRecord>(`/workspaces/${workspaceId}/services`, payload);
      setFormOpen(false);
      setNotice(editingService ? `${result.name} updated.` : `${result.name} added to the service catalog.`);
      await loadCatalog();
      setSelectedService(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Service could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function archiveService(service: ServiceRecord) {
    if (!workspaceId || !window.confirm(`Retire ${service.name}? The service and its history will remain in the catalog.`)) return;
    setSaving(true);
    setError("");
    try {
      const archived = await apiPost<ServiceRecord>(`/workspaces/${workspaceId}/services/${service.id}/archive`);
      setSelectedService(archived);
      setNotice(`${service.name} has been retired.`);
      await loadCatalog();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Service could not be retired.");
    } finally {
      setSaving(false);
    }
  }

  async function createTeam(slug: string, name: string, description: string) {
    if (!workspaceId) return;
    setSaving(true);
    setError("");
    try {
      const team = await apiPost<ServiceTeam>(`/workspaces/${workspaceId}/teams`, { slug, name, description: description || null });
      await loadCatalog();
      updateDraft("owner_team_id", team.id);
      setNotice(`${team.name} team created.`);
      setTeamOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Team could not be created.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <PageFrame eyebrow="WORKSPACE · SERVICE OWNERSHIP" title="Service Catalog" description="A single, owned inventory of the services running in this workspace and the paths teams use to create them.">
      {(error || notice) && <div className={`catalog-notice ${error ? "is-error" : "is-success"}`} role={error ? "alert" : "status"}><span>{error ? <CircleAlert size={15} /> : <Check size={15} />}{error || notice}</span><button onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss message"><X size={14} /></button></div>}

      {!workspaceId ? (
        <section className="catalog-empty"><div className="catalog-empty-icon"><Layers3 size={21} /></div><h2>Select a workspace</h2><p>Choose a workspace from the sidebar to view its services, ownership teams and golden paths.</p></section>
      ) : (
        <>
          <div className="catalog-summary">
            <SummaryCard label="Catalog services" value={summary?.total ?? "—"} detail="In this workspace" icon={<Boxes size={17} />} />
            <SummaryCard label="Production" value={summary?.production ?? "—"} detail="Live lifecycle" icon={<ShieldCheck size={17} />} tone="green" />
            <SummaryCard label="Experimental" value={summary?.experimental ?? "—"} detail="In active development" icon={<Workflow size={17} />} tone="violet" />
            <SummaryCard label="Ownership teams" value={summary?.ownership_teams ?? "—"} detail="Clear service ownership" icon={<Layers3 size={17} />} tone="blue" />
          </div>

          <div className="catalog-toolbar">
            <div className="catalog-tabs" role="tablist" aria-label="Service catalog views">
              <button role="tab" aria-selected={tab === "services"} className={tab === "services" ? "is-active" : ""} onClick={() => setTab("services")}>Services <span>{catalog?.total ?? 0}</span></button>
              <button role="tab" aria-selected={tab === "golden-paths"} className={tab === "golden-paths" ? "is-active" : ""} onClick={() => setTab("golden-paths")}>Golden paths <span>{goldenPaths.length}</span></button>
            </div>
            <div className="catalog-actions">
              {tab === "services" && <button className="catalog-secondary-button" onClick={() => setTeamOpen(true)}><Settings2 size={14} /> Manage teams</button>}
              {canWrite && tab === "services" && <button className="catalog-primary-button" onClick={() => openCreate()}><Plus size={15} /> Register service</button>}
            </div>
          </div>

          {tab === "services" ? (
            <section className="catalog-panel">
              <div className="catalog-filter-row">
                <label className="catalog-search"><Search size={15} /><input aria-label="Search services" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by service, owner, system or repository…" /></label>
                <label className="catalog-lifecycle-filter"><span>Lifecycle</span><select value={lifecycle} onChange={(event) => setLifecycle(event.target.value)}><option value="all">All lifecycles</option><option value="production">Production</option><option value="experimental">Experimental</option><option value="deprecated">Deprecated</option><option value="retired">Retired</option></select><ChevronDown size={13} /></label>
                <label className="catalog-lifecycle-filter"><span>Owner</span><select value={ownerTeamId} onChange={(event) => setOwnerTeamId(event.target.value)}><option value="all">All teams</option>{teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select><ChevronDown size={13} /></label>
              </div>
              <div className="catalog-table-wrap">
                <table className="catalog-table">
                  <thead><tr><th>Service</th><th>Owner team</th><th>Lifecycle</th><th>Criticality</th><th>Repository</th><th>Updated</th><th><span className="sr-only">Details</span></th></tr></thead>
                  <tbody>
                    {services.map((service) => <tr key={service.id}>
                      <td><button className="catalog-service-cell" onClick={() => setSelectedService(service)}><span className="catalog-service-mark"><Boxes size={16} /></span><span><strong>{service.name}</strong><small>{service.slug}{service.system ? ` · ${service.system}` : ""}</small></span></button></td>
                      <td><span className="catalog-owner"><span className="team-avatar">{service.owner_team_name?.slice(0, 1).toUpperCase() || "?"}</span>{service.owner_team_name || <span className="muted-cell">Unassigned</span>}</span></td>
                      <td><span className={`lifecycle-pill lifecycle-${service.lifecycle}`}>{service.lifecycle === "production" ? <span className="status-dot" /> : null}{lifecycleLabel(service.lifecycle)}</span></td>
                      <td><span className={`tier-pill tier-${service.criticality}`}>{criticalityLabel(service.criticality)}</span></td>
                      <td>{service.repository_url ? <a className="catalog-repo-link" href={service.repository_url} target="_blank" rel="noreferrer"><GitBranch size={13} />Repository<ExternalLink size={11} /></a> : <span className="muted-cell">Not connected</span>}</td>
                      <td className="catalog-date">{displayDate(service.updated_at)}</td>
                      <td><button className="catalog-open-button" aria-label={`View ${service.name}`} onClick={() => setSelectedService(service)}><ArrowUpRight size={15} /></button></td>
                    </tr>)}
                  </tbody>
                </table>
                {loading ? <div className="catalog-loading"><LoaderCircle size={17} /> Loading service catalog…</div> : services.length === 0 ? <div className="catalog-list-empty"><div className="catalog-empty-icon"><Boxes size={20} /></div><strong>{query || lifecycle !== "all" || ownerTeamId !== "all" ? "No services match these filters" : "Your catalog is ready"}</strong><p>{query || lifecycle !== "all" || ownerTeamId !== "all" ? "Try a different search term or filter." : "Register a service directly or start with a golden path to establish ownership and standards."}</p>{canWrite && !query && lifecycle === "all" && ownerTeamId === "all" && <button className="catalog-primary-button" onClick={() => { setTab("golden-paths"); }}><ArrowDownRight size={14} /> Explore golden paths</button>}</div> : null}
              </div>
              <div className="catalog-table-footer"><span>Showing {catalog?.total ? catalog.offset + 1 : 0}–{catalog ? catalog.offset + services.length : 0} of {catalog?.total ?? 0} services</span><div className="catalog-pagination"><button disabled={loading || page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>Previous</button><span>Page {page + 1} of {Math.max(1, Math.ceil((catalog?.total ?? 0) / 20))}</span><button disabled={loading || !catalog || (page + 1) * 20 >= catalog.total} onClick={() => setPage((value) => value + 1)}>Next</button></div><span><span className="status-dot" />Workspace scoped inventory</span></div>
            </section>
          ) : (
            <section className="golden-path-section">
              <div className="golden-path-intro"><div className="golden-path-intro-icon"><Workflow size={18} /></div><div><strong>Supported paths for new services</strong><p>Consistent starting points with explicit defaults and platform capabilities. Choosing a path registers the service metadata; repository scaffolding becomes available with a source control integration.</p></div></div>
              {loading ? <div className="catalog-loading"><LoaderCircle size={17} /> Loading golden paths…</div> : <div className="golden-path-grid">{goldenPaths.map((path) => <article className="golden-path-card" key={path.id}>
                <div className="golden-path-card-top"><span className={`golden-path-icon golden-icon-${path.category.toLowerCase()}`}><GoldenPathIcon icon={path.icon} /></span><span className="golden-path-category">{path.category}</span><span className="golden-path-version">v{path.version}</span></div>
                <h2>{path.name}</h2><p className="golden-path-description">{path.description}</p>
                <div className="golden-path-capabilities">{path.capabilities.slice(0, 4).map((capability) => <span key={capability}><Check size={12} />{capability}</span>)}</div>
                <div className="golden-path-footer"><span><span className="status-dot" />Available</span>{canWrite && <button className="catalog-primary-button" onClick={() => openCreate(path)}>Use this path <ArrowUpRight size={14} /></button>}</div>
              </article>)}{goldenPaths.length === 0 && <div className="catalog-list-empty"><strong>No golden paths are available</strong><p>Golden paths will appear here when configured by your platform team.</p></div>}</div>}
            </section>
          )}
        </>
      )}

      {formOpen && <ServiceFormModal
        draft={draft}
        teams={teams}
        path={selectedPath}
        editing={editingService}
        busy={saving}
        canCreateTeam={canWrite}
        onClose={() => setFormOpen(false)}
        onOpenTeams={() => setTeamOpen(true)}
        onChange={updateDraft}
        onSubmit={submitService}
      />}
      {teamOpen && <TeamModal teams={teams} busy={saving} canWrite={canWrite} onClose={() => setTeamOpen(false)} onCreate={createTeam} />}
      {selectedService && <ServiceDetails service={selectedService} canWrite={canWrite} busy={saving} onClose={() => setSelectedService(null)} onEdit={() => openEdit(selectedService)} onArchive={() => void archiveService(selectedService)} />}
    </PageFrame>
  );
}

function SummaryCard({ label, value, detail, icon, tone = "cyan" }: { label: string; value: string | number; detail: string; icon: React.ReactNode; tone?: string }) {
  return <article className="catalog-summary-card"><span className={`catalog-summary-icon tone-${tone}`}>{icon}</span><div><small>{label}</small><strong>{value}</strong><span>{detail}</span></div></article>;
}

function ServiceFormModal({
  draft,
  teams,
  path,
  editing,
  busy,
  canCreateTeam,
  onClose,
  onOpenTeams,
  onChange,
  onSubmit,
}: {
  draft: ServiceDraft;
  teams: ServiceTeam[];
  path: GoldenPath | null;
  editing: ServiceRecord | null;
  busy: boolean;
  canCreateTeam: boolean;
  onClose: () => void;
  onOpenTeams: () => void;
  onChange: <K extends keyof ServiceDraft>(key: K, value: ServiceDraft[K]) => void;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
}) {
  return <div className="catalog-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}><section className="catalog-modal service-form-modal" role="dialog" aria-modal="true" aria-labelledby="service-form-title">
    <div className="catalog-modal-heading"><span className="catalog-modal-heading-icon"><Boxes size={17} /></span><div><small>{editing ? "SERVICE CATALOG" : path ? `GOLDEN PATH · ${path.category.toUpperCase()}` : "SERVICE CATALOG"}</small><h2 id="service-form-title">{editing ? "Edit service" : path ? `Create from ${path.name}` : "Register service"}</h2><p>{path ? "Review the ownership and configuration details before adding this service." : "Record ownership, repository and operational metadata for a workspace service."}</p></div><button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={17} /></button></div>
    <form className="catalog-form" onSubmit={onSubmit}>
      <div className="catalog-form-grid">
        <label>Service name<input required minLength={2} maxLength={180} value={draft.name} onChange={(event) => { onChange("name", event.target.value); if (!editing) onChange("slug", slugFromName(event.target.value)); }} placeholder="Payments API" /></label>
        <label>Service slug<input required pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" maxLength={100} value={editing ? editing.slug : draft.slug} disabled={Boolean(editing)} onChange={(event) => onChange("slug", event.target.value)} placeholder="payments-api" /></label>
        <label className="catalog-form-wide">Description<textarea maxLength={5000} rows={2} value={draft.description} onChange={(event) => onChange("description", event.target.value)} placeholder="What this service does and who depends on it" /></label>
        <label>Owner team<div className="catalog-select-wrap"><select required value={draft.owner_team_id} onChange={(event) => onChange("owner_team_id", event.target.value)}><option value="">Select owner team</option>{teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select><ChevronDown size={14} /></div></label>
        <label>System <span className="form-optional">optional</span><input maxLength={120} value={draft.system} onChange={(event) => onChange("system", event.target.value)} placeholder="Commerce platform" /></label>
        <label className="catalog-form-wide">Repository URL <span className="form-optional">optional</span><input maxLength={500} type="url" value={draft.repository_url} onChange={(event) => onChange("repository_url", event.target.value)} placeholder="https://github.com/acme/payments-api" /></label>
        <label className="catalog-form-wide">Documentation URL <span className="form-optional">optional</span><input maxLength={500} type="url" value={draft.documentation_url} onChange={(event) => onChange("documentation_url", event.target.value)} placeholder="https://docs.acme.dev/payments" /></label>
        <label>Lifecycle<div className="catalog-select-wrap"><select value={draft.lifecycle} onChange={(event) => onChange("lifecycle", event.target.value as ServiceLifecycle)}><option value="experimental">Experimental</option><option value="production">Production</option><option value="deprecated">Deprecated</option><option value="retired">Retired</option></select><ChevronDown size={14} /></div></label>
        <label>Criticality<div className="catalog-select-wrap"><select value={draft.criticality} onChange={(event) => onChange("criticality", event.target.value as ServiceCriticality)}><option value="tier_0">Tier 0 · Critical</option><option value="tier_1">Tier 1 · High</option><option value="tier_2">Tier 2 · Standard</option><option value="tier_3">Tier 3 · Low</option></select><ChevronDown size={14} /></div></label>
        <label className="catalog-form-wide">Tags <span className="form-optional">comma separated</span><input value={draft.tags} onChange={(event) => onChange("tags", event.target.value)} placeholder="payments, customer-facing, api" /></label>
      </div>
      {path && path.config_schema.length > 0 && <div className="golden-config-block"><div><strong>Path configuration</strong><small>These values are stored as service metadata for this template version.</small></div><div className="catalog-form-grid">{path.config_schema.map((field) => <label key={field.key}>{field.label}{field.type === "select" ? <div className="catalog-select-wrap"><select required value={draft.metadata[field.key] || ""} onChange={(event) => onChange("metadata", { ...draft.metadata, [field.key]: event.target.value })}>{(field.options || []).map((option) => <option key={option} value={option}>{option}</option>)}</select><ChevronDown size={14} /></div> : <input value={draft.metadata[field.key] || ""} onChange={(event) => onChange("metadata", { ...draft.metadata, [field.key]: event.target.value })} />}</label>)}</div></div>}
      {teams.length === 0 && <div className="catalog-inline-warning"><CircleAlert size={14} /><span>Create an ownership team before registering a service.</span>{canCreateTeam && <button type="button" onClick={onOpenTeams}>Create team</button>}</div>}
      {path && <div className="golden-path-disclosure"><ShieldCheck size={14} /><span>This registers catalog metadata only. Repository scaffolding and deployment are not started.</span></div>}
      <div className="catalog-form-footer"><button type="button" className="catalog-secondary-button" onClick={onClose}>Cancel</button><button type="submit" className="catalog-primary-button" disabled={busy || !teams.length}>{busy && <LoaderCircle size={14} className="spin-icon" />}{editing ? "Save changes" : path ? "Add service to catalog" : "Register service"}</button></div>
    </form>
  </section></div>;
}

function TeamModal({ teams, busy, canWrite, onClose, onCreate }: { teams: ServiceTeam[]; busy: boolean; canWrite: boolean; onClose: () => void; onCreate: (slug: string, name: string, description: string) => void }) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  return <div className="catalog-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}><section className="catalog-modal team-modal" role="dialog" aria-modal="true" aria-labelledby="team-modal-title">
    <div className="catalog-modal-heading"><span className="catalog-modal-heading-icon"><Layers3 size={17} /></span><div><small>OWNERSHIP MODEL</small><h2 id="team-modal-title">Service teams</h2><p>Keep every catalog entry attached to an accountable team.</p></div><button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={17} /></button></div>
    <div className="team-list">{teams.map((team) => <div className="team-list-row" key={team.id}><span className="team-avatar">{team.name.slice(0, 1).toUpperCase()}</span><span><strong>{team.name}</strong><small>{team.slug} · {team.service_count} {team.service_count === 1 ? "service" : "services"}</small></span></div>)}{teams.length === 0 && <div className="settings-empty">No ownership teams have been created for this workspace.</div>}</div>
    {canWrite ? <form className="team-create-form" onSubmit={(event) => { event.preventDefault(); onCreate(slug, name.trim(), description.trim()); }}><div className="team-create-heading"><strong>Create team</strong><small>Teams are scoped to the selected workspace.</small></div><label>Team name<input required minLength={2} maxLength={120} value={name} onChange={(event) => { setName(event.target.value); setSlug(slugFromName(event.target.value).slice(0, 80)); }} placeholder="Payments" /></label><label>Team slug<input required pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" maxLength={80} value={slug} onChange={(event) => setSlug(event.target.value)} placeholder="payments" /></label><label>Description <span className="form-optional">optional</span><input maxLength={2000} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Team responsibilities" /></label><button className="catalog-primary-button" disabled={busy}>{busy && <LoaderCircle size={14} className="spin-icon" />}<Plus size={14} /> Create team</button></form> : <div className="catalog-readonly-note">Workspace admin or platform engineer permission is required to create teams.</div>}
  </section></div>;
}

function ServiceDetails({ service, canWrite, busy, onClose, onEdit, onArchive }: { service: ServiceRecord; canWrite: boolean; busy: boolean; onClose: () => void; onEdit: () => void; onArchive: () => void }) {
  return <div className="catalog-drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><aside className="catalog-drawer" role="dialog" aria-modal="true" aria-labelledby="service-details-title">
    <header className="catalog-drawer-header"><div className="catalog-drawer-icon"><Boxes size={19} /></div><button className="icon-button" onClick={onClose} aria-label="Close service details"><X size={17} /></button></header>
    <div className="catalog-drawer-title"><small>SERVICE DETAILS</small><h2 id="service-details-title">{service.name}</h2><p>{service.slug}{service.system ? ` · ${service.system}` : ""}</p><span className={`lifecycle-pill lifecycle-${service.lifecycle}`}>{lifecycleLabel(service.lifecycle)}</span></div>
    <div className="catalog-drawer-section"><h3>Ownership</h3><div className="service-detail-owner"><span className="team-avatar">{service.owner_team_name?.slice(0, 1).toUpperCase() || "?"}</span><span><strong>{service.owner_team_name || "Unassigned"}</strong><small>Responsible team</small></span></div><DetailRow label="Criticality" value={criticalityLabel(service.criticality)} /><DetailRow label="System" value={service.system || "Not specified"} /></div>
    <div className="catalog-drawer-section"><h3>Source and documentation</h3><DetailLink label="Repository" value={service.repository_url} /><DetailLink label="Documentation" value={service.documentation_url} /></div>
    <div className="catalog-drawer-section"><h3>Catalog metadata</h3><DetailRow label="Golden path" value={service.golden_path_slug || "Registered directly"} /><DetailRow label="Created" value={displayDate(service.created_at)} /><DetailRow label="Last updated" value={displayDate(service.updated_at)} />{service.tags.length > 0 && <div className="service-detail-tags">{service.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}{service.description && <p className="service-detail-description">{service.description}</p>}</div>
    {Object.keys(service.metadata).length > 0 && <div className="catalog-drawer-section"><h3>Path configuration</h3>{Object.entries(service.metadata).map(([key, value]) => <DetailRow key={key} label={key.replaceAll("_", " ")} value={String(value)} />)}</div>}
    {canWrite && <footer className="catalog-drawer-actions"><button className="catalog-secondary-button" onClick={onEdit}><Settings2 size={14} /> Edit service</button>{service.lifecycle !== "retired" && <button className="catalog-danger-button" disabled={busy} onClick={onArchive}>Retire service</button>}</footer>}
  </aside></div>;
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return <div className="service-detail-row"><span>{label}</span><strong>{value}</strong></div>;
}

function DetailLink({ label, value }: { label: string; value: string | null }) {
  return <div className="service-detail-row"><span>{label}</span>{value ? <a href={value} target="_blank" rel="noreferrer">Open link <ExternalLink size={12} /></a> : <strong className="muted-cell">Not connected</strong>}</div>;
}
