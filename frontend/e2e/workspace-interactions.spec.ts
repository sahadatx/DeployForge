import { expect, test, type Page } from "@playwright/test";

const workspaceId = "workspace-interactions";
const timestamp = "2026-11-06T10:00:00Z";

async function mockPlatform(page: Page) {
  const requests: URL[] = [];
  const services = Array.from({ length: 125 }, (_, index) => ({
    id: `service-${index + 1}`, workspace_id: workspaceId, name: index === 124 ? "Checkout API" : `Service ${index + 1}`,
    slug: `service-${index + 1}`, lifecycle: "production", owner_team_name: "Platform", created_at: timestamp, updated_at: timestamp,
  }));
  const releases = Array.from({ length: 125 }, (_, index) => ({
    id: `release-${index + 1}`, workspace_id: workspaceId, service_id: "service-125", environment_id: "environment-1",
    version: `v-${index + 1}`, status: "failed", source_ref: "main", requested_by: "another-engineer", attempt_count: 1, max_attempts: 3,
    failure_code: "build_failed", failure_message: "Build evidence was rejected.", created_at: timestamp, updated_at: timestamp,
  }));
  const alert = { id: "alert-1", workspace_id: workspaceId, slo_id: "slo-1", summary: "Checkout error budget breach", status: "firing", details: {}, created_at: timestamp };
  const run = { id: "run-1", workspace_id: workspaceId, target_id: "target-1", application_name: "checkout-staging", action_kind: "deploy", status: "blocked", failure_message: "Waiting for reconciliation", created_at: timestamp, updated_at: timestamp };
  await page.addInitScript((id) => localStorage.setItem("deployforge.workspace", id), workspaceId);
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api\/v1/, "");
    requests.push(url);
    if (route.request().method() !== "GET") return route.fulfill({ status: 405, json: { error: { message: "This review only uses mocked reads." } } });
    if (path === "/auth/me") return route.fulfill({ json: { user: { id: "interaction-admin", display_name: "Platform Engineer", email: "engineer@example.test", is_platform_admin: true, email_verified: true }, memberships: [{ workspace_id: workspaceId, role: "workspace_admin" }], csrf_token: "interaction-csrf" } });
    if (path === "/workspaces") return route.fulfill({ json: [workspaceId, "workspace-other"].map((id) => ({ id, name: id, slug: id, created_at: timestamp })) });
    if (path === "/system/status") return route.fulfill({ json: { api: "healthy", database: "healthy", worker: "healthy", checked_at: timestamp, worker_last_seen_at: timestamp } });
    if (path === "/audit-logs") {
      if (url.searchParams.get("workspace_id") === "workspace-other") return route.fulfill({ status: 503, json: { error: { message: "Other workspace history is unavailable." } } });
      return route.fulfill({ json: [{ id: "audit-original", actor_type: "user", actor_id: "engineer", action: "deployment.created", resource_type: "deployment", resource_id: "release-1", outcome: "succeeded", details: { actor_name: "Original Workspace Engineer" }, created_at: timestamp }] });
    }
    const resource = path.replace(/^\/workspaces\/[^/]+\//, "");
    const offset = Number(url.searchParams.get("offset") || 0);
    const limit = Number(url.searchParams.get("limit") || 100);
    if (resource === "services") {
      const query = (url.searchParams.get("search") || "").toLowerCase();
      if (query === "broken") return route.fulfill({ status: 503, json: { error: { message: "Search service is temporarily unavailable." } } });
      const matches = services.filter((service) => !query || `${service.name} ${service.slug}`.toLowerCase().includes(query));
      return route.fulfill({ json: { items: matches.slice(offset, offset + limit), total: matches.length, offset, limit } });
    }
    if (resource === "services/summary") return route.fulfill({ json: { total: 125, production: 125, experimental: 0, ownership_teams: 1 } });
    if (resource === "environments") return route.fulfill({ json: [{ id: "environment-1", name: "Staging", slug: "staging", environment_type: "staging" }] });
    if (resource === "deployments") return route.fulfill({ json: releases.slice(offset, offset + limit) });
    if (resource === "alerts") return route.fulfill({ json: offset ? [] : [alert] });
    if (resource === "gitops-runs") return route.fulfill({ json: offset ? [] : [run] });
    if (resource === "gitops-targets") return route.fulfill({ json: [{ id: "target-1", environment_id: "environment-1" }] });
    if (resource === "slos") return route.fulfill({ json: [{ id: "slo-1", service_id: "service-125", environment_id: "environment-1", name: "Checkout availability" }] });
    return route.fulfill({ json: [] });
  });
  return { requests };
}

test("global search clears old results, reports failures and restores keyboard focus", async ({ page }) => {
  await mockPlatform(page);
  await page.goto("/deployments");
  const trigger = page.getByRole("button", { name: "Search pages", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Search DeployForge pages" });
  const search = dialog.getByRole("textbox", { name: "Search pages and workspace resources" });
  await expect(search).toBeFocused();
  await search.fill("checkout");
  await expect(dialog.getByRole("button", { name: /Checkout API/ })).toBeVisible();
  await search.fill("broken");
  await expect(dialog.getByRole("button", { name: /Checkout API/ })).toHaveCount(0);
  await expect(dialog.getByRole("alert")).toHaveText("Search service is temporarily unavailable.");
  await expect(dialog.getByText("No matching pages or workspace resources.")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await page.getByRole("button", { name: "Request release", exact: true }).click();
  await page.keyboard.press("Control+k");
  await expect(page.locator("dialog[open]")).toHaveCount(1);
  await expect(page.getByRole("dialog", { name: "Request a release", exact: true })).toBeVisible();
});

test("release pagination and service choices include records beyond the first100", async ({ page }) => {
  const platform = await mockPlatform(page);
  await page.goto("/deployments");
  const rows = page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "Release", exact: true }) }).locator("tbody tr");
  await expect(rows).toHaveCount(25);
  await expect(page.getByRole("navigation", { name: "Pagination for releases", exact: true })).toContainText("of 125");
  await page.getByRole("combobox", { name: "Current page of releases", exact: true }).selectOption({ value: "4" });
  await expect(rows.first()).toContainText("v-101");
  await page.getByRole("searchbox", { name: "Search releases", exact: true }).fill("v-125");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("v-125");
  await page.getByRole("button", { name: "Request release", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Request a release", exact: true });
  await expect(dialog.getByRole("combobox", { name: "Service", exact: true }).locator('option[value="service-125"]')).toHaveText("Checkout API · service-125");
  expect(platform.requests.some((url) => url.pathname.endsWith("/deployments") && url.searchParams.get("offset") === "100")).toBe(true);
});

test("a failed new workspace cannot retain the previous workspace audit records", async ({ page }) => {
  await mockPlatform(page);
  await page.goto("/audit-logs");
  await expect(page.getByText("Original Workspace Engineer", { exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Workspace", exact: true }).selectOption("workspace-other");
  await expect(page.getByRole("alert")).toContainText("Other workspace history is unavailable.");
  await expect(page.getByText("Original Workspace Engineer", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("table")).toHaveCount(0);
});

test("incident View actions open the selected source record in a details drawer", async ({ page }) => {
  await mockPlatform(page);
  await page.goto("/incidents");
  const scenarios = [
    { button: "View alert Checkout error budget breach", title: "Checkout error budget breach", id: "alert-1" },
    { button: "View failed release v-1", title: "Release v-1", id: "release-1" },
    { button: "View checkout-staging rollout", title: "checkout-staging", id: "run-1" },
  ];
  for (const scenario of scenarios) {
    if (scenario.id === "run-1") await page.getByRole("button", { name: "Show GitOps failures: 1", exact: false }).click();
    await page.getByRole("button", { name: scenario.button, exact: true }).click();
    const dialog = page.getByRole("dialog", { name: scenario.title, exact: true });
    await expect(dialog).toContainText(scenario.id);
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page).toHaveURL(/\/incidents$/);
  }
});

test("unavailable audit and incident feeds do not claim zero events or a healthy empty queue", async ({ page }) => {
  await mockPlatform(page);
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/audit-logs") || /\/(alerts|deployments|gitops-runs)$/.test(path)) {
      return route.fulfill({ status: 503, json: { error: { message: "Source data is unavailable." } } });
    }
    await route.fallback();
  });
  await page.goto("/audit-logs");
  await expect(page.getByText("Audit history is unavailable. Retry when the connection is restored.", { exact: true })).toBeVisible();
  await expect(page.locator(".audit-summary-card strong")).toHaveText(["—", "—", "—", "—"]);
  await expect(page.getByText("No audit events have been recorded for this scope yet.", { exact: true })).toHaveCount(0);
  await page.goto("/incidents");
  await expect(page.getByText("Response signals are unavailable", { exact: true })).toBeVisible();
  await expect(page.locator(".metric-grid-4 .metric-card strong")).toHaveText(["—", "—", "—", "—"]);
  await expect(page.getByText("No active reliability signals", { exact: true })).toHaveCount(0);
});
