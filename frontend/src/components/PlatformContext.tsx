import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { AuthSession, WorkspaceMembership } from "../lib/api";

type PlatformContextValue = {
  session: AuthSession;
  workspaceId: string;
  environmentId: string;
  role: WorkspaceMembership["role"] | null;
  isPlatformAdmin: boolean;
  canOperate: boolean;
  canManageWorkspace: boolean;
};

const PlatformContext = createContext<PlatformContextValue | null>(null);

export function PlatformProvider({ session, children }: { session: AuthSession; children: ReactNode }) {
  const [workspaceId, setWorkspaceId] = useState(() => localStorage.getItem("deployforge.workspace") || "");
  const [environmentId, setEnvironmentId] = useState(() => localStorage.getItem("deployforge.environment") || "");

  useEffect(() => {
    const onWorkspace = (event: Event) => {
      setWorkspaceId((event as CustomEvent<string>).detail || "");
      setEnvironmentId("");
    };
    const onEnvironment = (event: Event) => setEnvironmentId((event as CustomEvent<string>).detail || "");
    const onStorage = () => {
      setWorkspaceId(localStorage.getItem("deployforge.workspace") || "");
      setEnvironmentId(localStorage.getItem("deployforge.environment") || "");
    };
    window.addEventListener("deployforge:workspace-changed", onWorkspace);
    window.addEventListener("deployforge:environment-changed", onEnvironment);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("deployforge:workspace-changed", onWorkspace);
      window.removeEventListener("deployforge:environment-changed", onEnvironment);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  const value = useMemo<PlatformContextValue>(() => {
    const isPlatformAdmin = session.user.is_platform_admin;
    const role = session.memberships.find((membership) => membership.workspace_id === workspaceId)?.role || null;
    return {
      session,
      workspaceId,
      environmentId,
      role,
      isPlatformAdmin,
      canOperate: isPlatformAdmin || role === "workspace_admin" || role === "platform_engineer",
      canManageWorkspace: isPlatformAdmin || role === "workspace_admin",
    };
  }, [environmentId, session, workspaceId]);

  return <PlatformContext.Provider value={value}>{children}</PlatformContext.Provider>;
}

export function usePlatformContext() {
  const value = useContext(PlatformContext);
  if (!value) throw new Error("Platform context is unavailable");
  return value;
}
