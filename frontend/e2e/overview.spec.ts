import { mkdir } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";

const workspaceId = "workspace-overview";
const createdAt = "2026-11-06T12:00:00Z";

function release(id: string, service: string, environment: string, status: string, created: string) {
  return { id, workspace_id: workspaceId, service_id: service, environment_id: environment, version: id, source_ref: "main", status, requested_by: "engineer", idempotency_key: null, attempt_count: 1, max_attempts: 3, started_at: null, finished_at: null, approved_by: null, approved_at: null, failure_code: null, failure_message: null, created_at: created, updated_at: created };
}

async function mockOverview(page: Page) {
  const requests: URLSearchParams[] = [];
  const releases = [
    release("v-validating", "service-checkout", "environment-staging", "validating", "2026-11-06T00:00:00Z"),
    release("v-approved", "service-checkout", "environment-staging", "approved", "2026-11-05T23:59:59Z"),
    release("v-review", "service-checkout", "environment-staging", "awaiting_release_approval", "2026-11-04T10:00:00Z"),
    release("v-failed", "service-payments", "environment-staging", "failed", "2026-11-06T23:59:59.999Z"),
    release("v-success", "service-checkout", "environment-staging", "succeeded", "2026-11-04T11:00:00Z"),
    release("v-production", "service-checkout", "environment-production", "succeeded", "2026-11-06T10:00:00Z"),
  ];
  await page.clock.setFixedTime(new Date(createdAt));
  await page.addInitScript((id) => {
    localStorage.setItem("deployforge.workspace", id);
    localStorage.setItem("deployforge.environment", "environment-staging");
  }, workspaceId);
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api\/v1/, "");
    if (path === "/auth/me") {
      await route.fulfill({ json: { user: { id: "engineer", email: "engineer@example.test", display_name: "Platform Engineer", email_verified: true, is_platform_admin: true, active: true, created_at: createdAt }, memberships: [{ workspace_id: workspaceId, role: "workspace_admin" }], csrf_token: "test-csrf" } });
    } else if (path === "/system/status") {
      await route.fulfill({ json: { api: "healthy", database: "healthy", worker: "healthy", worker_last_seen_at: createdAt, checked_at: createdAt } });
    } else if (path === "/workspaces") {
      await route.fulfill({ json: [{ id: workspaceId, name: "Delivery Platform", slug: "delivery-platform", description: null, created_at: createdAt }] });
    } else if (path === "/workspaces/" + workspaceId + "/environments") {
      await route.fulfill({ json: [{ id: "environment-staging", name: "Staging", environment_type: "staging" }, { id: "environment-production", name: "Production", environment_type: "production" }, { id: "environment-empty", name: "Preview", environment_type: "preview" }] });
    } else if (path === "/workspaces/" + workspaceId + "/services/summary") {
      await route.fulfill({ json: { total: 2, production: 2, experimental: 0, ownership_teams: 1 } });
    } else if (path === "/workspaces/" + workspaceId + "/services") {
      await route.fulfill({ json: { items: [{ id: "service-checkout", name: "Checkout API", slug: "checkout-api" }, { id: "service-payments", name: "Payments API", slug: "payments-api" }], total: 2, limit: 100, offset: 0 } });
    } else if (path === "/workspaces/" + workspaceId + "/deployments") {
      requests.push(new URLSearchParams(url.searchParams));
      const environment = url.searchParams.get("environment_id");
      await route.fulfill({ json: releases.filter((item) => !environment || item.environment_id === environment) });
    } else if (path === "/audit-logs") {
      await route.fulfill({ json: [{ id: "audit-overview", workspace_id: workspaceId, action: "workspace.member_added", actor_type: "user", actor_id: "engineer", resource_type: "workspace", resource_id: workspaceId, outcome: "succeeded", details: {}, created_at: createdAt }] });
    } else if (["alerts", "slos", "gitops-runs"].some((resource) => path === "/workspaces/" + workspaceId + "/" + resource)) {
      await route.fulfill({ json: [] });
    } else {
      await route.fulfill({ status: 404, json: { error: { message: "Unexpected overview fixture request: " + path } } });
    }
  });
  return requests;
}

test("overview filters include UTC date boundaries and real in-progress states while KPI and trend views stay scoped", async ({ page }) => {
  const requests = await mockOverview(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/overview");
  const metric = (label: string) => page.getByRole("button", { name: new RegExp("^Show " + label + ":") });
  await expect(metric("recent releases").locator("strong")).toHaveText("5");
  await expect(metric("in progress").locator("strong")).toHaveText("2");
  await expect(metric("pending approval").locator("strong")).toHaveText("1");
  await expect.poll(() => requests.some((params) => params.get("environment_id") === "environment-staging" && params.get("limit") === "100")).toBe(true);

  await page.getByLabel("Release start date in UTC", { exact: true }).fill("2026-11-06");
  await page.getByLabel("Release end date in UTC", { exact: true }).fill("2026-11-06");
  await expect(metric("recent releases").locator("strong")).toHaveText("2");
  await page.getByRole("combobox", { name: "Filter overview by service" }).selectOption("service-checkout");
  await expect(metric("recent releases").locator("strong")).toHaveText("1");
  await metric("in progress").click();
  const activity = page.getByRole("table", { name: /Five newest matching release requests/ });
  await expect(activity.locator("tbody tr")).toHaveCount(1);
  await expect(activity).toContainText("v-validating");
  await expect(page.getByRole("combobox", { name: "Filter overview by release status" })).toHaveValue("in_progress");

  await page.getByRole("combobox", { name: "Release trend window" }).selectOption("7");
  await page.getByText("View daily data", { exact: true }).click();
  const daily = page.getByRole("table", { name: /Daily release creation dates/ });
  await expect(daily.locator("tbody tr")).toHaveCount(7);
  await expect(daily.getByRole("row").filter({ has: page.getByRole("rowheader", { name: "2026-11-06", exact: true }) }).locator("th, td")).toHaveText(["2026-11-06", "0", "0", "1", "1"]);
  await expect(daily.getByRole("row").filter({ has: page.getByRole("rowheader", { name: "2026-11-05", exact: true }) }).locator("th, td")).toHaveText(["2026-11-05", "0", "0", "0", "0"]);
  await expect(page.getByText(/These are not historical state snapshots/)).toBeVisible();
  await page.getByRole("button", { name: "View release v-validating", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Release v-validating", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "Reset filters", exact: true }).click();
  await expect(metric("recent releases").locator("strong")).toHaveText("5");
  await expect(page.getByLabel("Release start date in UTC", { exact: true })).toHaveValue("");
  if (process.env.DEPLOYFORGE_CAPTURE_QA === "1") {
    await mkdir("/tmp/deployforge-qa", { recursive: true });
    await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); window.scrollTo(0, 0); });
    await page.screenshot({ path: "/tmp/deployforge-qa/overview-desktop.png", fullPage: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.setViewportSize({ width: 667, height: 375 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  if (process.env.DEPLOYFORGE_CAPTURE_QA === "1") {
    await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); window.scrollTo(0, 0); });
    await page.screenshot({ path: "/tmp/deployforge-qa/overview-narrow.png", fullPage: true });
  }
});

test("changing environment clears overview filters and shows the correct scoped empty state", async ({ page }) => {
  await mockOverview(page);
  await page.goto("/overview");
  await expect(page.getByRole("button", { name: "Show recent releases: 5", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Filter overview by service" }).selectOption("service-checkout");
  await page.getByRole("combobox", { name: "Filter overview by release status" }).selectOption("failed");
  await page.getByRole("combobox", { name: "Environment", exact: true }).selectOption("environment-empty");
  await expect(page.getByRole("combobox", { name: "Filter overview by service" })).toHaveValue("");
  await expect(page.getByRole("combobox", { name: "Filter overview by release status" })).toHaveValue("");
  await expect(page.getByRole("button", { name: "Show recent releases: 0", exact: true })).toBeVisible();
  await expect(page.getByText("No deployments in this environment", { exact: true })).toBeVisible();
  await expect(page.getByRole("table", { name: /Five newest matching release requests/ })).toHaveCount(0);
});

