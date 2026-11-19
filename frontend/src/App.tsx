import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";

const OverviewPage = lazy(() => import("./pages/OverviewPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const AuditLogsPage = lazy(() => import("./pages/AuditLogsPage"));
const ServicesPage = lazy(() => import("./pages/ServicesPage"));
const DeploymentsPage = lazy(() => import("./pages/DeploymentsPage"));
const GitOpsPage = lazy(() => import("./pages/GitOpsPage"));
const EnvironmentsPage = lazy(() => import("./pages/EnvironmentsPage"));
const InfrastructurePage = lazy(() => import("./pages/InfrastructurePage"));
const PoliciesPage = lazy(() => import("./pages/PoliciesPage"));
const ObservabilityPage = lazy(() => import("./pages/ObservabilityPage"));
const IncidentsPage = lazy(() => import("./pages/IncidentsPage"));

function PageLoading() {
  return (
    <main className="auth-loading">
      <div className="brand-mark" aria-hidden="true"><span>DF</span></div>
      <strong>Opening DeployForge</strong>
      <span>Loading the selected workspace page…</span>
    </main>
  );
}

export default function App() {
  return (
    <Suspense fallback={<PageLoading />}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<OverviewPage />} />
          <Route path="/audit-logs" element={<AuditLogsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/services" element={<ServicesPage />} />
          <Route path="/deployments" element={<DeploymentsPage />} />
          <Route path="/gitops" element={<GitOpsPage />} />
          <Route path="/environments" element={<EnvironmentsPage />} />
          <Route path="/infrastructure" element={<InfrastructurePage />} />
          <Route path="/policies" element={<PoliciesPage />} />
          <Route path="/observability" element={<ObservabilityPage />} />
          <Route path="/incidents" element={<IncidentsPage />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Route>
      </Routes>
    </Suspense>
  );
}

