import { useEffect, useState } from "react";
import { ChevronDown, Layers3 } from "lucide-react";
import { apiGet, type Environment, type Workspace } from "../lib/api";

const WORKSPACE_EVENT = "deployforge:workspace-changed";

export function WorkspaceSelector() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState(() => localStorage.getItem("deployforge.workspace") || "");
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    apiGet<Workspace[]>("/workspaces", controller.signal)
      .then((items) => {
        setWorkspaces(items);
        setUnavailable(false);
        if (!items.some((item) => item.id === workspaceId)) {
          setWorkspaceId(items[0]?.id || "");
        }
      })
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) setUnavailable(true);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    localStorage.setItem("deployforge.workspace", workspaceId);
    localStorage.removeItem("deployforge.environment");
    window.dispatchEvent(new CustomEvent(WORKSPACE_EVENT, { detail: workspaceId }));
  }, [workspaceId]);

  return (
    <label className="context-select workspace-select">
      <span className="context-icon"><Layers3 size={15} /></span>
      <select
        aria-label="Workspace"
        value={workspaceId}
        disabled={workspaces.length === 0}
        onChange={(event) => setWorkspaceId(event.target.value)}
      >
        {workspaces.length === 0 ? (
          <option value="">{unavailable ? "Workspace data unavailable" : "No workspace configured"}</option>
        ) : (
          workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)
        )}
      </select>
      <ChevronDown size={14} />
    </label>
  );
}

export function EnvironmentSelector() {
  const [workspaceId, setWorkspaceId] = useState(() => localStorage.getItem("deployforge.workspace") || "");
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [environmentId, setEnvironmentId] = useState(() => localStorage.getItem("deployforge.environment") || "");

  useEffect(() => {
    const onWorkspaceChanged = (event: Event) => {
      const customEvent = event as CustomEvent<string>;
      setWorkspaceId(customEvent.detail || "");
      setEnvironmentId("");
    };
    window.addEventListener(WORKSPACE_EVENT, onWorkspaceChanged);
    return () => window.removeEventListener(WORKSPACE_EVENT, onWorkspaceChanged);
  }, []);

  useEffect(() => {
    if (!workspaceId) {
      setEnvironments([]);
      setEnvironmentId("");
      return;
    }
    const controller = new AbortController();
    apiGet<Environment[]>(`/workspaces/${workspaceId}/environments`, controller.signal)
      .then((items) => {
        setEnvironments(items);
        if (!items.some((item) => item.id === environmentId)) setEnvironmentId(items[0]?.id || "");
      })
      .catch(() => setEnvironments([]));
    return () => controller.abort();
  }, [workspaceId]);

  useEffect(() => {
    if (environmentId) localStorage.setItem("deployforge.environment", environmentId);
    else localStorage.removeItem("deployforge.environment");
    window.dispatchEvent(new CustomEvent("deployforge:environment-changed", { detail: environmentId }));
  }, [environmentId]);

  return (
    <label className="context-select environment-select">
      <select
        aria-label="Environment"
        value={environmentId}
        disabled={environments.length === 0}
        onChange={(event) => setEnvironmentId(event.target.value)}
      >
        {environments.length === 0 ? (
          <option value="">No environment selected</option>
        ) : (
          environments.map((environment) => <option key={environment.id} value={environment.id}>{environment.name}</option>)
        )}
      </select>
      <ChevronDown size={14} />
    </label>
  );
}

