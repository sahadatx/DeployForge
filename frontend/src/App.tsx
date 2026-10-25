import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import OverviewPage from "./pages/OverviewPage";
import SettingsPage from "./pages/SettingsPage";
import AuditLogsPage from "./pages/AuditLogsPage";
import ServicesPage from "./pages/ServicesPage";
import DeploymentsPage from "./pages/DeploymentsPage";
import GitOpsPage from "./pages/GitOpsPage";
import EnvironmentsPage from "./pages/EnvironmentsPage";
import InfrastructurePage from "./pages/InfrastructurePage";
import PoliciesPage from "./pages/PoliciesPage";
import ObservabilityPage from "./pages/ObservabilityPage";
import IncidentsPage from "./pages/IncidentsPage";

export default function App() {
  return (
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
  );
}

