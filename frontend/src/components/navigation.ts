import {
  Activity,
  Boxes,
  ClipboardList,
  Cloud,
  GitBranch,
  LayoutDashboard,
  Rocket,
  ScrollText,
  Settings,
  ShieldCheck,
  Siren,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type NavigationItem = {
  label: string;
  path: string;
  icon: LucideIcon;
  description: string;
};

export type NavigationSection = {
  label: string;
  items: NavigationItem[];
};

export const navigation: NavigationSection[] = [
  {
    label: "Workspace",
    items: [
      { label: "Overview", path: "/overview", icon: LayoutDashboard, description: "Platform health and recent activity" },
      { label: "Service Catalog", path: "/services", icon: Boxes, description: "Services, owners and golden paths" },
      { label: "Deployments", path: "/deployments", icon: Rocket, description: "Release history, approvals and rollbacks" },
      { label: "GitOps", path: "/gitops", icon: GitBranch, description: "Application sync and configuration drift" },
      { label: "Environments", path: "/environments", icon: Cloud, description: "Development, staging and production" },
      { label: "Infrastructure", path: "/infrastructure", icon: ClipboardList, description: "Infrastructure plans and drift" },
      { label: "Policies", path: "/policies", icon: ShieldCheck, description: "Rules, violations and exceptions" },
    ],
  },
  {
    label: "Reliability",
    items: [
      { label: "Observability", path: "/observability", icon: Activity, description: "Metrics, logs, traces and SLOs" },
      { label: "Incidents", path: "/incidents", icon: Siren, description: "Response, recovery and follow-up" },
    ],
  },
  {
    label: "Governance",
    items: [
      { label: "Audit Logs", path: "/audit-logs", icon: ScrollText, description: "Approvals and privileged activity" },
      { label: "Settings", path: "/settings", icon: Settings, description: "Users, roles and integrations" },
    ],
  },
];

export const allNavigationItems = navigation.flatMap((section) => section.items);

