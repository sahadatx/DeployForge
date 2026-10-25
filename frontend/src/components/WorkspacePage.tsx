import { useEffect, useState, type ReactNode } from "react";
import { Activity, AlertTriangle, RefreshCw } from "lucide-react";
import { apiGet, type Workspace } from "../lib/api";
import { usePlatformContext } from "./PlatformContext";
import { PageFrame } from "./PageFrame";

export function useWorkspaceResource<T>(path: string | null, interval = 20_000) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    if (!path) {
      setData(null);
      setLoading(false);
      setError("");
      return;
    }
    let active = true;
    const controller = new AbortController();
    const load = async () => {
      try {
        const result = await apiGet<T>(path, controller.signal);
        if (active) {
          setData(result);
          setError("");
        }
      } catch (caught) {
        if (active && !(caught instanceof DOMException && caught.name === "AbortError")) {
          setError(caught instanceof Error ? caught.message : "The platform data could not be loaded.");
        }
      } finally {
        if (active) setLoading(false);
      }
    };
    setLoading(true);
    void load();
    const timer = interval > 0 ? window.setInterval(() => void load(), interval) : undefined;
    return () => {
      active = false;
      controller.abort();
      if (timer) window.clearInterval(timer);
    };
  }, [interval, path, revision]);

  return { data, loading, error, refresh: () => setRevision((value) => value + 1), setData };
}

export function useWorkspaceCatalog() {
  const { workspaceId } = usePlatformContext();
  const result = useWorkspaceResource<Workspace[]>("/workspaces", 60_000);
  const workspace = result.data?.find((item) => item.id === workspaceId) || null;
  return { ...result, workspace };
}

export function WorkspacePage({
  eyebrow,
  title,
  description,
  actions,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const { workspaceId } = usePlatformContext();
  const { workspace, loading, error } = useWorkspaceCatalog();
  return (
    <PageFrame eyebrow={eyebrow} title={title} description={description}>
      <div className="operational-toolbar">
        <span className="context-chip"><span className="context-chip-dot" />{workspace?.name || (workspaceId ? "Selected workspace" : "No workspace selected")}</span>
        <div className="operational-toolbar-actions">{actions}<span className="live-indicator"><span className="status-dot" /> Live data</span></div>
      </div>
      {!workspaceId && <div className="workspace-empty"><Activity size={17} /><span><strong>Select a workspace</strong><small>Choose a workspace from the sidebar to view its platform data.</small></span></div>}
      {workspaceId && error && <div className="settings-notice is-error resource-error" role="alert"><AlertTriangle size={14} />{error}{loading && <span> · Refreshing</span>}</div>}
      {children}
    </PageFrame>
  );
}

export function RefreshButton({ onClick, busy = false }: { onClick: () => void; busy?: boolean }) {
  return <button className="catalog-secondary-button" onClick={onClick} disabled={busy}><RefreshCw size={13} className={busy ? "spin" : ""} /> Refresh</button>;
}

export function ResourceErrors({ resources }: { resources: Array<{ label: string; error: string }> }) {
  const failures = resources.filter((resource) => resource.error);
  if (!failures.length) return null;
  return <div className="resource-error-list">{failures.map((resource) => <div className="settings-notice is-error resource-error" role="alert" key={resource.label}><AlertTriangle size={14} /><span><strong>{resource.label}:</strong> {resource.error}</span></div>)}</div>;
}

export function ResourceStatus({ value }: { value: string | null | undefined }) {
  const status = (value || "unknown").toLowerCase();
  const tone = ["succeeded", "healthy", "approved", "resolved", "synced", "available"].includes(status)
    ? "success"
    : ["failed", "error", "breaching", "blocked", "rejected", "degraded", "firing"].includes(status)
      ? "danger"
      : ["running", "queued", "deploying", "building", "validating", "pending", "acknowledged", "planned", "drift_detected"].includes(status) || status.startsWith("awaiting_")
        ? "warning"
        : "neutral";
  return <span className={`resource-status ${tone}`}><span className="status-dot" />{status.replaceAll("_", " ")}</span>;
}

export function LoadingOrEmpty({ loading, empty, label = "No records yet" }: { loading: boolean; empty: boolean; label?: string }) {
  if (loading) return <div className="resource-loading"><span className="spinner" />Loading live data…</div>;
  if (empty) return <div className="resource-empty"><div className="empty-state-icon"><Activity size={17} /></div><strong>{label}</strong><p>New records will appear here when this workspace starts using the corresponding workflow.</p></div>;
  return null;
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const preference = localStorage.getItem("deployforge.timezone") || "system";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", ...(preference === "system" ? {} : { timeZone: preference }) }).format(date);
}
