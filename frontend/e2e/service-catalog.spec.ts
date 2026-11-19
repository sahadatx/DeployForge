import { mkdir } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import type { ServiceRecord, WorkspaceMember } from "../src/lib/api";

const workspaceId = "workspace-catalog-qa";
const createdAt = "2026-11-04T09:00:00Z";
test.use({ timezoneId: "Asia/Dhaka" });

async function capture(page: Page, name: string) {
  if (process.env.DEPLOYFORGE_CAPTURE_QA !== "1") return;
  await mkdir("/tmp/deployforge-qa", { recursive: true });
  const dialogOpen = await page.locator("dialog[open]").count() > 0;
  await page.screenshot({ path: "/tmp/deployforge-qa/" + name + ".png", fullPage: !dialogOpen });
}

async function mockWorkspace(page: Page) {
  const services: ServiceRecord[] = Array.from({ length: 31 }, (_, index) => ({
    id: "service-" + (index + 1),
    workspace_id: workspaceId,
    slug: "service-" + String(index + 1).padStart(2, "0"),
    name: "Service " + String(index + 1).padStart(2, "0"),
    description: "Customer-facing service with explicit platform ownership.",
    system: "Commerce",
    owner_team_id: index % 2 ? "team-commerce" : "team-platform",
    owner_team_name: index % 2 ? "Commerce" : "Platform",
    repository_url: "https://example.test/repository/" + (index + 1),
    documentation_url: "https://example.test/documentation/" + (index + 1),
    lifecycle: index < 16 ? "production" : "experimental",
    criticality: "tier_1",
    tags: ["api", "customer-facing"],
    metadata: { language: "TypeScript" },
    golden_path_id: null,
    golden_path_slug: null,
    created_by: "qa-admin",
    created_at: createdAt,
    updated_at: createdAt,
  }));
  const members: WorkspaceMember[] = Array.from({ length: 27 }, (_, index) => ({
    membership_id: "membership-" + (index + 1),
    user: {
      id: "member-" + (index + 1),
      display_name: "Member " + String(index + 1).padStart(2, "0"),
      email: "member" + (index + 1) + "@example.test",
      email_verified: index % 3 !== 0,
      is_platform_admin: index === 0,
      active: true,
      created_at: createdAt,
    },
    role: index === 0 ? "workspace_admin" : index % 2 ? "auditor" : "platform_engineer",
    created_at: createdAt,
  }));
  const serviceQueries: URLSearchParams[] = [];
  const serviceWrites: Array<Record<string, unknown>> = [];
  const memberWrites: Array<Record<string, unknown>> = [];

  await page.addInitScript((id) => localStorage.setItem("deployforge.workspace", id), workspaceId);
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\/v1/, "");
    if (path === "/auth/me") {
      await route.fulfill({ json: { user: { ...members[0].user, id: "qa-admin", display_name: "QA Administrator" }, memberships: [{ workspace_id: workspaceId, role: "workspace_admin" }], csrf_token: "qa-csrf" } });
    } else if (path === "/auth/config") {
      await route.fulfill({ json: { oidc_enabled: true, development_login_enabled: false, authentication_required: true } });
    } else if (path === "/system/status") {
      await route.fulfill({ json: { api: "healthy", database: "healthy", worker: "healthy", worker_last_seen_at: createdAt, checked_at: createdAt } });
    } else if (path === "/workspaces") {
      await route.fulfill({ json: [{ id: workspaceId, slug: "commerce-platform", name: "Commerce Platform", description: null, created_at: createdAt }] });
    } else if (path === "/workspaces/" + workspaceId + "/services" && request.method() === "POST") {
      serviceWrites.push(request.postDataJSON() as Record<string, unknown>);
      await route.fulfill({ status: 409, json: { error: { message: "A service with this slug already exists." } } });
    } else if (path === "/workspaces/" + workspaceId + "/services") {
      serviceQueries.push(new URLSearchParams(url.searchParams));
      const limit = Number(url.searchParams.get("limit") || 25);
      const offset = Number(url.searchParams.get("offset") || 0);
      const query = (url.searchParams.get("search") || "").toLowerCase();
      const lifecycle = url.searchParams.get("lifecycle");
      const owner = url.searchParams.get("owner_team_id");
      const matching = services.filter((service) => (!query || (service.name + " " + service.slug).toLowerCase().includes(query)) && (!lifecycle || service.lifecycle === lifecycle) && (!owner || service.owner_team_id === owner));
      await route.fulfill({ json: { items: matching.slice(offset, offset + limit), total: matching.length, limit, offset } });
    } else if (path === "/workspaces/" + workspaceId + "/services/summary") {
      await route.fulfill({ json: { total: 31, production: 16, experimental: 15, ownership_teams: 2 } });
    } else if (path === "/workspaces/" + workspaceId + "/teams") {
      await route.fulfill({ json: [{ id: "team-platform", workspace_id: workspaceId, slug: "platform", name: "Platform", description: null, service_count: 16, created_at: createdAt, updated_at: createdAt }, { id: "team-commerce", workspace_id: workspaceId, slug: "commerce", name: "Commerce", description: null, service_count: 15, created_at: createdAt, updated_at: createdAt }] });
    } else if (path === "/workspaces/" + workspaceId + "/golden-paths") {
      await route.fulfill({ json: [] });
    } else if (path === "/workspaces/" + workspaceId + "/repository-provisionings") {
      await route.fulfill({ json: [] });
    } else if (path === "/workspaces/" + workspaceId + "/members" && request.method() === "POST") {
      const body = request.postDataJSON() as { email: string; role: WorkspaceMember["role"] };
      memberWrites.push(body);
      const member = { ...members[0], membership_id: "membership-granted", role: body.role, user: { ...members[0].user, id: "member-granted", email: body.email, display_name: "New Engineer", is_platform_admin: false } };
      members.push(member);
      await route.fulfill({ status: 201, json: member });
    } else if (path === "/workspaces/" + workspaceId + "/members") {
      await route.fulfill({ json: members });
    } else if (["environments", "deployments", "alerts", "gitops-runs", "build-runs"].some((resource) => path === "/workspaces/" + workspaceId + "/" + resource)) {
      await route.fulfill({ json: [] });
    } else {
      await route.fulfill({ status: 404, json: { error: { message: "Unexpected mocked API request: " + path } } });
    }
  });
  return { serviceQueries, serviceWrites, memberWrites };
}

test("catalog server pagination, KPI filters, details and failed form input remain usable at desktop and narrow widths", async ({ page }) => {
  const mock = await mockWorkspace(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/services");
  const table = page.getByRole("table", { name: /Workspace service catalog/ });
  await expect(table.locator("tbody tr")).toHaveCount(25);
  await capture(page, "service-catalog-desktop");

  await page.getByRole("combobox", { name: "Services per page" }).selectOption("10");
  await expect(table.locator("tbody tr")).toHaveCount(10);
  await page.getByRole("button", { name: "Next page of services", exact: true }).click();
  await expect(table.locator("tbody tr").first()).toContainText("Service 11");
  await expect.poll(() => mock.serviceQueries.some((params) => params.get("limit") === "10" && params.get("offset") === "10")).toBe(true);

  await page.locator(".catalog-summary-card").filter({ hasText: "Production" }).click();
  await expect(page.getByRole("combobox", { name: "Lifecycle" })).toHaveValue("production");
  await expect(page.getByRole("navigation", { name: "Pagination for services" })).toContainText("of 16");
  await expect.poll(() => mock.serviceQueries.some((params) => params.get("lifecycle") === "production" && params.get("offset") === "0")).toBe(true);
  await page.getByRole("searchbox", { name: "Search services" }).fill("Service 03");
  await expect(table.locator("tbody tr")).toHaveCount(1);
  await page.getByRole("button", { name: "Reset filters", exact: true }).click();
  await expect(page.getByRole("searchbox", { name: "Search services" })).toHaveValue("");
  await expect(page.getByRole("combobox", { name: "Lifecycle" })).toHaveValue("all");
  await expect(table.locator("tbody tr")).toHaveCount(10);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await capture(page, "service-catalog-narrow");
  await page.getByRole("button", { name: "View Service 01", exact: true }).click();
  const details = page.getByRole("dialog", { name: "Service 01", exact: true });
  await expect(details).toBeVisible();
  await expect(details.getByRole("heading", { name: "Ownership", exact: true })).toBeVisible();
  await expect(details).toHaveJSProperty("open", true);
  await capture(page, "service-details-narrow");
  await page.keyboard.press("Escape");
  await expect(details).not.toBeVisible();

  await page.setViewportSize({ width: 667, height: 375 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "Register service", exact: true }).click();
  const form = page.getByRole("dialog", { name: "Register service", exact: true });
  await form.getByRole("button", { name: "Register service", exact: true }).click();
  expect(mock.serviceWrites).toHaveLength(0);
  await form.getByRole("textbox", { name: "Service name", exact: true }).fill("Checkout API");
  await form.getByRole("textbox", { name: "Description", exact: true }).fill("Preserve this operational description after an API error.");
  await form.getByRole("button", { name: "Register service", exact: true }).click();
  await expect(form.getByRole("alert")).toHaveText("A service with this slug already exists.");
  await expect(form.getByRole("textbox", { name: "Service name", exact: true })).toHaveValue("Checkout API");
  await expect(form.getByRole("textbox", { name: "Description", exact: true })).toHaveValue("Preserve this operational description after an API error.");
  expect(mock.serviceWrites).toHaveLength(1);
  await capture(page, "service-form-error-desktop");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.keyboard.press("Escape");
  await expect(form).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await page.keyboard.press("Escape");
  await expect(form).not.toBeVisible();
});

test("workspace member filters, details and grant access drawer work at desktop and narrow widths", async ({ page }) => {
  const mock = await mockWorkspace(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/settings");
  const table = page.getByRole("table", { name: /Workspace members, assigned roles/ });
  await expect(table.locator("tbody tr")).toHaveCount(25);
  await page.getByRole("combobox", { name: "Filter workspace members by role", exact: true }).selectOption("auditor");
  await expect(table.locator("tbody tr")).toHaveCount(13);
  await page.getByRole("button", { name: "View Member 02", exact: true }).click();
  const details = page.getByRole("dialog", { name: "Member 02", exact: true });
  await expect(details).toBeVisible();
  await expect(details.getByText("member2@example.test", { exact: true })).toBeVisible();
  await capture(page, "member-details-desktop");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Reset filters for workspace members", exact: true }).click();
  await expect(table.locator("tbody tr")).toHaveCount(25);
  await capture(page, "settings-desktop");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await capture(page, "settings-narrow");
  await page.getByRole("button", { name: "Grant access", exact: true }).click();
  const grant = page.getByRole("dialog", { name: "Grant workspace access", exact: true });
  await expect(grant).toBeVisible();
  await grant.getByRole("textbox", { name: "User email", exact: true }).fill("new.engineer@example.test");
  await grant.getByRole("combobox", { name: "Workspace role", exact: true }).selectOption("auditor");
  await capture(page, "grant-access-narrow");
  await grant.getByRole("button", { name: "Grant access", exact: true }).click();
  await expect(grant).not.toBeVisible();
  expect(mock.memberWrites).toEqual([{ email: "new.engineer@example.test", role: "auditor" }]);
  await page.getByRole("searchbox", { name: "Search workspace members", exact: true }).fill("new.engineer@example.test");
  await expect(table.locator("tbody tr")).toHaveCount(1);
  await expect(table.locator("tbody tr")).toContainText("New Engineer");
});

test("changing workspace clears catalog filters and removes old rows before the next catalog loads", async ({ page }) => {
  await mockWorkspace(page);
  await page.addInitScript(() => localStorage.setItem("deployforge.timezone", "UTC"));
  const nextWorkspaceId = "workspace-next-catalog";
  const nextQueries: URLSearchParams[] = [];
  let finishNextCatalog = () => {};
  const nextCatalogReady = new Promise<void>((resolve) => { finishNextCatalog = resolve; });
  await page.route("**/api/v1/workspaces", (route) => route.fulfill({ json: [{ id: workspaceId, name: "Commerce Platform", slug: "commerce-platform", created_at: createdAt }, { id: nextWorkspaceId, name: "Payments Platform", slug: "payments-platform", created_at: createdAt }] }));
  await page.route("**/api/v1/workspaces/" + nextWorkspaceId + "/**", async (route) => {
    const url = new URL(route.request().url());
    const resource = url.pathname.split("/").at(-1);
    if (resource === "services") {
      nextQueries.push(new URLSearchParams(url.searchParams));
      await nextCatalogReady;
      await route.fulfill({ json: { items: [{ id: "service-payments-next", workspace_id: nextWorkspaceId, slug: "payments-api", name: "Payments API", description: null, system: null, owner_team_id: "team-payments-next", owner_team_name: "Payments", repository_url: null, documentation_url: null, lifecycle: "experimental", criticality: "tier_2", tags: [], metadata: {}, golden_path_id: null, golden_path_slug: null, created_by: null, created_at: createdAt, updated_at: createdAt }], total: 1, limit: 25, offset: 0 } });
    } else if (resource === "summary") {
      await route.fulfill({ json: { total: 1, production: 0, experimental: 1, ownership_teams: 1 } });
    } else if (resource === "teams") {
      await route.fulfill({ json: [{ id: "team-payments-next", workspace_id: nextWorkspaceId, name: "Payments", slug: "payments", description: null, service_count: 1, created_at: createdAt, updated_at: createdAt }] });
    } else {
      await route.fulfill({ json: [] });
    }
  });
  await page.goto("/services");
  await expect(page.locator(".catalog-date").first()).toContainText("9:00 AM");
  await page.getByRole("searchbox", { name: "Search services", exact: true }).fill("Service 03");
  await page.getByRole("combobox", { name: "Lifecycle" }).selectOption("production");
  await page.getByRole("combobox", { name: "Owner" }).selectOption("team-platform");
  await expect(page.getByRole("button", { name: "View Service 03", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Workspace", exact: true }).selectOption(nextWorkspaceId);
  await expect(page.getByRole("button", { name: "View Service 03", exact: true })).toHaveCount(0);
  await expect(page.getByRole("searchbox", { name: "Search services", exact: true })).toHaveValue("");
  await expect(page.getByRole("combobox", { name: "Lifecycle" })).toHaveValue("all");
  await expect(page.getByRole("combobox", { name: "Owner" })).toHaveValue("all");
  await expect.poll(() => nextQueries.length).toBeGreaterThan(0);
  finishNextCatalog();
  await expect(page.getByRole("button", { name: "View Payments API", exact: true })).toBeVisible();
  expect(nextQueries.every((params) => !params.has("search") && !params.has("lifecycle") && !params.has("owner_team_id") && params.get("offset") === "0")).toBe(true);
});

