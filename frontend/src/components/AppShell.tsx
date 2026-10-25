import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Activity, BookOpen, Boxes, ChevronDown, ChevronLeft, CircleUserRound, Command, LogOut, Menu, Search, X } from "lucide-react";
import { allNavigationItems, navigation } from "./navigation";
import { PlatformStatus } from "./PlatformStatus";
import { EnvironmentSelector, WorkspaceSelector } from "./WorkspaceControls";
import { PlatformProvider } from "./PlatformContext";
import AuthGate from "./AuthGate";
import { ApiError, apiGet, apiPost, setCsrfToken, type AuthConfig, type AuthSession, type Deployment, type ServiceCatalog, type SloAlert } from "../lib/api";

type AuthState = "loading" | "ready" | "signed-out" | "unavailable";

function userInitials(name: string) {
  return name.split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "U";
}

export default function AppShell() {
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("deployforge.sidebar-collapsed") === "true");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [serviceSearchResults, setServiceSearchResults] = useState<ServiceCatalog["items"]>([]);
  const [deploymentSearchResults, setDeploymentSearchResults] = useState<Deployment[]>([]);
  const [alertSearchResults, setAlertSearchResults] = useState<SloAlert[]>([]);
  const [serviceSearchLoading, setServiceSearchLoading] = useState(false);
  const [navigationBadges, setNavigationBadges] = useState<Record<string, number>>({});
  const [profileOpen, setProfileOpen] = useState(false);
  const [authState, setAuthState] = useState<AuthState>("loading");
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null);
  const [authSession, setAuthSession] = useState<AuthSession | null>(null);
  const [selectedWorkspace, setSelectedWorkspace] = useState(() => localStorage.getItem("deployforge.workspace") || "");
  const [authErrorCode, setAuthErrorCode] = useState(() => new URLSearchParams(window.location.search).get("error") || undefined);
  const location = useLocation();
  const navigate = useNavigate();
  const currentPage = allNavigationItems.find((item) => item.path === location.pathname);

  const loadAuthentication = useCallback(async () => {
    setAuthState("loading");
    try {
      const session = await apiGet<AuthSession>("/auth/me");
      setCsrfToken(session.csrf_token);
      setAuthSession(session);
      setAuthErrorCode(undefined);
      setAuthState("ready");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setCsrfToken("");
        setAuthSession(null);
        try {
          setAuthConfig(await apiGet<AuthConfig>("/auth/config"));
          setAuthState("signed-out");
        } catch {
          setAuthState("unavailable");
        }
      } else {
        setAuthState("unavailable");
      }
    }
  }, []);

  useEffect(() => { void loadAuthentication(); }, [loadAuthentication]);

  useEffect(() => {
    const onWorkspaceChanged = (event: Event) => setSelectedWorkspace((event as CustomEvent<string>).detail || "");
    window.addEventListener("deployforge:workspace-changed", onWorkspaceChanged);
    return () => window.removeEventListener("deployforge:workspace-changed", onWorkspaceChanged);
  }, []);

  useEffect(() => {
    if (!selectedWorkspace) { setNavigationBadges({}); return; }
    let active = true;
    const loadBadges = async () => {
      try {
        const [deployments, alerts, gitopsRuns] = await Promise.all([
          apiGet<Deployment[]>(`/workspaces/${selectedWorkspace}/deployments?limit=100`),
          apiGet<SloAlert[]>(`/workspaces/${selectedWorkspace}/alerts?limit=100`),
          apiGet<{ status: string }[]>(`/workspaces/${selectedWorkspace}/gitops-runs?limit=100`),
        ]);
        if (active) setNavigationBadges({
          "/deployments": deployments.filter((item) => ["awaiting_approval", "awaiting_release_approval", "failed"].includes(item.status)).length,
          "/incidents": alerts.filter((item) => item.status === "firing" || item.status === "acknowledged").length + deployments.filter((item) => item.status === "failed").length + gitopsRuns.filter((item) => item.status === "failed" || item.status === "blocked").length,
        });
      } catch {
        if (active) setNavigationBadges({});
      }
    };
    void loadBadges();
    const timer = window.setInterval(() => void loadBadges(), 30_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [selectedWorkspace]);

  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
      }
      if (event.key === "Escape") setSearchOpen(false);
    };
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, []);

  useEffect(() => {
    const term = searchText.trim();
    if (!searchOpen || !selectedWorkspace || term.length < 2) {
      setServiceSearchResults([]);
      setDeploymentSearchResults([]);
      setAlertSearchResults([]);
      setServiceSearchLoading(false);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setServiceSearchLoading(true);
      const params = new URLSearchParams({ search: term, limit: "5" });
      Promise.all([
        apiGet<ServiceCatalog>(`/workspaces/${selectedWorkspace}/services?${params}`, controller.signal),
        apiGet<Deployment[]>(`/workspaces/${selectedWorkspace}/deployments?limit=100`, controller.signal),
        apiGet<SloAlert[]>(`/workspaces/${selectedWorkspace}/alerts?limit=100`, controller.signal),
      ])
        .then(([services, deployments, alerts]) => {
          const normalized = term.toLowerCase();
          setServiceSearchResults(services.items);
          setDeploymentSearchResults(deployments.filter((item) => `${item.version} ${item.source_ref || ""} ${item.id}`.toLowerCase().includes(normalized)).slice(0, 5));
          setAlertSearchResults(alerts.filter((item) => `${item.summary} ${item.status}`.toLowerCase().includes(normalized)).slice(0, 5));
        })
        .catch((error: unknown) => {
          if (!(error instanceof DOMException && error.name === "AbortError")) {
            setServiceSearchResults([]); setDeploymentSearchResults([]); setAlertSearchResults([]);
          }
        })
        .finally(() => { if (!controller.signal.aborted) setServiceSearchLoading(false); });
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [searchOpen, searchText, selectedWorkspace]);

  function toggleCollapsed() {
    setCollapsed((value) => {
      localStorage.setItem("deployforge.sidebar-collapsed", String(!value));
      return !value;
    });
  }

  if (authState === "loading") {
    return <main className="auth-loading"><div className="brand-mark" aria-hidden="true"><span>DF</span></div><strong>Connecting to DeployForge</strong><span>Checking your platform session…</span></main>;
  }

  if (authState === "unavailable") {
    return <main className="auth-loading"><div className="brand-mark" aria-hidden="true"><span>DF</span></div><strong>Platform API unavailable</strong><span>DeployForge could not confirm the current session.</span><button className="auth-secondary-button" onClick={() => void loadAuthentication()}>Retry connection</button></main>;
  }

  if (authState === "signed-out" || !authSession) {
    return <AuthGate config={authConfig || { oidc_enabled: false, development_login_enabled: false, authentication_required: true }} errorCode={authErrorCode} onSignedIn={(session) => { setCsrfToken(session.csrf_token); setAuthSession(session); setAuthState("ready"); setAuthErrorCode(undefined); }} />;
  }

  const visibleNavigation = navigation;
  const visibleItems = visibleNavigation.flatMap((section) => section.items);
  const visibleSearchResults = visibleItems.filter((item) => item.label.toLowerCase().includes(searchText.trim().toLowerCase()));

  async function signOut() {
    try {
      await apiPost<void>("/auth/logout");
    } catch {
      // Clear the local view even if the session has already expired server-side.
    } finally {
      setCsrfToken("");
      setAuthSession(null);
      setAuthState("signed-out");
      setAuthConfig(await apiGet<AuthConfig>("/auth/config").catch(() => ({ oidc_enabled: false, development_login_enabled: false, authentication_required: true })));
      navigate("/overview", { replace: true });
    }
  }

  return (
    <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      {mobileOpen && <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}
      <aside className={`sidebar ${mobileOpen ? "mobile-open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true"><span>DF</span></div>
          {!collapsed && <div className="brand-copy"><strong>DeployForge</strong><span>ENTERPRISE PLATFORM</span></div>}
          <button className="sidebar-toggle" onClick={toggleCollapsed} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            <ChevronLeft size={16} />
          </button>
        </div>

        {!collapsed && <div className="workspace-selector-wrap"><WorkspaceSelector /></div>}

        <nav className="primary-nav" aria-label="Primary navigation">
          {visibleNavigation.map((section) => (
            <div className="nav-section" key={section.label}>
              {!collapsed && <div className="nav-section-label">{section.label}</div>}
              {section.items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    title={collapsed ? `${item.label}${navigationBadges[item.path] ? ` · ${navigationBadges[item.path]}` : ""}` : undefined}
                    aria-label={item.label}
                    onClick={() => setMobileOpen(false)}
                    className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}
                  >
                    <Icon size={18} strokeWidth={1.8} />
                    {!collapsed && <span>{item.label}</span>}
                    {!collapsed && Boolean(navigationBadges[item.path]) && <span className={`nav-badge ${item.path === "/incidents" ? "nav-badge-danger" : ""}`}>{navigationBadges[item.path]}</span>}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="sidebar-bottom">
          {!collapsed && <PlatformStatus />}
          <div className="profile-menu-wrap">
            <button className="user-row profile-trigger" title={authSession.user.email} aria-expanded={profileOpen} onClick={() => setProfileOpen((value) => !value)}>
              <span className="avatar">{userInitials(authSession.user.display_name)}</span>
              {!collapsed && <span className="user-copy"><strong>{authSession.user.display_name}</strong><span>{authSession.user.is_platform_admin ? "Platform administrator" : authSession.user.email}</span></span>}
              {!collapsed && <ChevronDown size={14} className="profile-chevron" />}
            </button>
            {profileOpen && <div className="profile-menu">
              <NavLink to="/settings" onClick={() => setProfileOpen(false)}><CircleUserRound size={14} />Profile & preferences</NavLink>
              <a href="/docs" target="_blank" rel="noreferrer"><BookOpen size={14} />API documentation</a>
              <button onClick={() => { setProfileOpen(false); void signOut(); }}><LogOut size={14} />Sign out</button>
            </div>}
          </div>
        </div>
      </aside>

      <main className="main-column">
        <header className="topbar">
          <button className="mobile-menu icon-button" aria-label="Open navigation" onClick={() => setMobileOpen(true)}><Menu size={19} /></button>
          <div className="breadcrumbs"><span>DeployForge</span><span className="breadcrumb-slash">/</span><strong>{currentPage?.label || "Overview"}</strong></div>
          <div className="topbar-actions">
            <EnvironmentSelector />
            <button className="search-trigger" onClick={() => setSearchOpen(true)} aria-label="Search pages">
              <Search size={16} /><span>Search</span><kbd><Command size={11} /> K</kbd>
            </button>
            <div className="topbar-avatar" title={authSession.user.email}>{userInitials(authSession.user.display_name)}</div>
          </div>
        </header>

        <div className="page-content"><PlatformProvider session={authSession}><Outlet /></PlatformProvider></div>
      </main>

      {searchOpen && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSearchOpen(false); }}>
          <section className="search-dialog" role="dialog" aria-modal="true" aria-label="Search DeployForge pages">
            <div className="search-dialog-input"><Search size={18} /><input autoFocus value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="Search platform pages…" /><button className="icon-button" onClick={() => setSearchOpen(false)} aria-label="Close search"><X size={17} /></button></div>
            <div className="search-results">
              {visibleSearchResults.length > 0 && <div className="search-group-label">Pages</div>}
              {visibleSearchResults.map((item) => {
                const Icon = item.icon;
                return <button key={item.path} className="search-result" onClick={() => { navigate(item.path); setSearchOpen(false); setSearchText(""); }}><Icon size={17} /><span><strong>{item.label}</strong><small>{item.description}</small></span></button>;
              })}
              {searchText.trim().length >= 2 && <div className="search-group-label">Services</div>}
              {serviceSearchResults.map((service) => <button key={service.id} className="search-result" onClick={() => { navigate(`/services?service=${encodeURIComponent(service.id)}`); setSearchOpen(false); setSearchText(""); }}><Boxes size={17} /><span><strong>{service.name}</strong><small>{service.owner_team_name || "Unassigned"} · {service.slug}</small></span></button>)}
              {deploymentSearchResults.length > 0 && <div className="search-group-label">Deployments</div>}
              {deploymentSearchResults.map((deployment) => <button key={deployment.id} className="search-result" onClick={() => { navigate("/deployments"); setSearchOpen(false); setSearchText(""); }}><Boxes size={17} /><span><strong>Release {deployment.version}</strong><small>{deployment.source_ref || deployment.id.slice(0, 8)} · {deployment.status}</small></span></button>)}
              {alertSearchResults.length > 0 && <div className="search-group-label">Reliability alerts</div>}
              {alertSearchResults.map((alert) => <button key={alert.id} className="search-result" onClick={() => { navigate("/incidents"); setSearchOpen(false); setSearchText(""); }}><Activity size={17} /><span><strong>{alert.summary}</strong><small>{alert.status} · {new Date(alert.created_at).toLocaleString()}</small></span></button>)}
              {serviceSearchLoading && <div className="search-empty">Searching services…</div>}
              {visibleSearchResults.length === 0 && serviceSearchResults.length === 0 && deploymentSearchResults.length === 0 && alertSearchResults.length === 0 && !serviceSearchLoading && <div className="search-empty">{searchText.trim().length >= 2 ? "No matching pages or workspace resources." : "Search pages, services, deployments and reliability alerts."}</div>}
            </div>
            <div className="search-dialog-footer">Search pages and live resources in the selected workspace</div>
          </section>
        </div>
      )}
      {mobileOpen && <button className="mobile-close" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><X size={18} /></button>}
    </div>
  );
}

