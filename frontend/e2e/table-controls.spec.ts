import { expect, test, type Page } from "@playwright/test";

const workspaceId = "workspace-table-controls";

function release(index: number) {
  return {
    id: `release-${index}`,
    workspace_id: workspaceId,
    service_id: "service-table-controls",
    environment_id: "environment-table-controls",
    version: `v-release-${String(index).padStart(2, "0")}`,
    source_ref: "main",
    status: index % 2 ? "failed" : "succeeded",
    requested_by: "another-engineer",
    attempt_count: 1,
    max_attempts: 3,
    started_at: "2026-11-04T10:00:00Z",
    finished_at: "2026-11-04T10:01:00Z",
    approved_by: null,
    approved_at: null,
    failure_code: null,
    failure_message: null,
    created_at: "2026-11-04T10:00:00Z",
    updated_at: "2026-11-04T10:01:00Z",
  };
}

async function mockPlatform(page: Page) {
  let releases = Array.from({ length: 30 }, (_, index) => release(index + 1));
  await page.addInitScript((selectedWorkspace: string) => {
    window.localStorage.setItem("deployforge.workspace", selectedWorkspace);
  }, workspaceId);

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api\/v1/, "");
    if (path === "/auth/me") {
      await route.fulfill({ json: {
        user: { id: "table-engineer", email: "engineer@example.test", display_name: "Platform Engineer", email_verified: true, is_platform_admin: true, active: true, created_at: "2026-11-04T09:00:00Z" },
        memberships: [{ workspace_id: workspaceId, role: "workspace_admin" }],
        csrf_token: "csrf-table-controls",
      } });
    } else if (path === "/workspaces") {
      await route.fulfill({ json: [{ id: workspaceId, slug: "table-controls", name: "Table Controls", description: null, created_at: "2026-11-04T09:00:00Z" }] });
    } else if (path === `/workspaces/${workspaceId}/deployments`) {
      await route.fulfill({ json: releases });
    } else if (path === `/workspaces/${workspaceId}/services`) {
      await route.fulfill({ json: { items: [{ id: "service-table-controls", name: "Checkout API", slug: "checkout-api" }], total: 1, limit: 100, offset: 0 } });
    } else if (path === `/workspaces/${workspaceId}/environments`) {
      await route.fulfill({ json: [{ id: "environment-table-controls", name: "Staging", environment_type: "staging" }] });
    } else if (["alerts", "gitops-runs", "build-runs"].some((resource) => path === `/workspaces/${workspaceId}/${resource}`)) {
      await route.fulfill({ json: [] });
    } else {
      await route.fulfill({ status: 404, json: { error: { message: `Unexpected test API request: ${path}` } } });
    }
  });

  return { retainFirstReleases: (count: number) => { releases = releases.slice(0, count); } };
}

function releaseRows(page: Page) {
  return page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "Release", exact: true }) }).locator("tbody tr");
}

async function refreshReleases(page: Page) {
  await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === `/api/v1/workspaces/${workspaceId}/deployments`),
    page.getByRole("button", { name: "Refresh", exact: true }).click(),
  ]);
}

test("a refreshed list preserves the selected page and clamps it when records disappear", async ({ page }) => {
  const platform = await mockPlatform(page);
  await page.goto("/deployments");
  await expect(releaseRows(page)).toHaveCount(25);
  await page.getByRole("button", { name: "Next page of releases", exact: true }).click();
  await expect(releaseRows(page)).toHaveCount(5);
  await expect(releaseRows(page).first()).toContainText("v-release-26");

  await refreshReleases(page);
  await expect(page.getByRole("combobox", { name: "Current page of releases", exact: true })).toHaveValue("1");
  await expect(releaseRows(page)).toHaveCount(5);
  await expect(releaseRows(page).first()).toContainText("v-release-26");

  platform.retainFirstReleases(5);
  await refreshReleases(page);
  await expect(page.getByRole("combobox", { name: "Current page of releases", exact: true })).toHaveValue("0");
  await expect(releaseRows(page)).toHaveCount(5);
  await expect(releaseRows(page).first()).toContainText("v-release-01");
  await expect(page.getByRole("navigation", { name: "Pagination for releases", exact: true })).toContainText("Showing 1–5 of 5");
});

test("search and status filters apply automatically, reset pagination, and clear together", async ({ page }) => {
  await mockPlatform(page);
  await page.goto("/deployments");
  const reset = page.getByRole("button", { name: "Reset filters for releases", exact: true });
  const search = page.getByRole("searchbox", { name: "Search releases", exact: true });
  const currentPage = page.getByRole("combobox", { name: "Current page of releases", exact: true });
  await expect(reset).toBeDisabled();
  await page.getByRole("button", { name: "Next page of releases", exact: true }).click();
  await search.fill("v-release-30");
  await expect(releaseRows(page)).toHaveCount(1);
  await expect(releaseRows(page).first()).toContainText("v-release-30");
  await expect(currentPage).toHaveValue("0");
  await reset.click();
  await expect(search).toHaveValue("");
  await expect(releaseRows(page)).toHaveCount(25);
  await expect(reset).toBeDisabled();

  await page.getByRole("button", { name: "Next page of releases", exact: true }).click();
  const status = page.getByRole("combobox", { name: "Filter releases by status", exact: true });
  await status.selectOption("failed");
  await expect(currentPage).toHaveValue("0");
  await expect(releaseRows(page)).toHaveCount(15);
  await expect(releaseRows(page).filter({ hasText: "failed" })).toHaveCount(15);
  await search.fill("does-not-exist");
  await expect(page.getByText("No releases match this filter.", { exact: true })).toBeVisible();
  await reset.click();
  await expect(status).toHaveValue("");
  await expect(search).toHaveValue("");
  await expect(releaseRows(page)).toHaveCount(25);

  await page.getByRole("combobox", { name: "Rows per page for releases", exact: true }).selectOption("10");
  await expect(releaseRows(page)).toHaveCount(10);
  await currentPage.selectOption({ value: "2" });
  await expect(releaseRows(page).first()).toContainText("v-release-21");
});

test("the release form drawer supports cancel, Escape, and returning keyboard focus", async ({ page }) => {
  await mockPlatform(page);
  await page.goto("/deployments");
  const trigger = page.getByRole("button", { name: "Request release", exact: true });
  await trigger.click();
  const drawer = page.getByRole("dialog", { name: "Request a release", exact: true });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("combobox", { name: "Service", exact: true })).toBeFocused();
  await drawer.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(drawer).toHaveCount(0);
  await expect(trigger).toBeFocused();

  await trigger.click();
  await expect(drawer).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("switching workspace hides previous releases and their actions while the next workspace is loading", async ({ page }) => {
  await mockPlatform(page);
  const nextWorkspaceId = "workspace-next";
  let resolveNextReleases = () => {};
  const pendingReleases = new Promise<void>((resolve) => { resolveNextReleases = resolve; });
  await page.route("**/api/v1/workspaces", (route) => route.fulfill({ json: [
    { id: workspaceId, slug: "table-controls", name: "Table Controls", description: null, created_at: "2026-11-04T09:00:00Z" },
    { id: nextWorkspaceId, slug: "next-workspace", name: "Next Workspace", description: null, created_at: "2026-11-04T09:00:00Z" },
  ] }));
  await page.route(`**/api/v1/workspaces/${nextWorkspaceId}/**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/deployments")) {
      await pendingReleases;
      await route.fulfill({ json: [{ ...release(99), id: "next-release", workspace_id: nextWorkspaceId, service_id: "next-service", environment_id: "next-environment", version: "v-next-workspace", status: "queued" }] });
    } else if (path.endsWith("/services")) {
      await route.fulfill({ json: { items: [{ id: "next-service", name: "Payments API", slug: "payments-api" }], total: 1, limit: 100, offset: 0 } });
    } else if (path.endsWith("/environments")) {
      await route.fulfill({ json: [{ id: "next-environment", name: "Production", environment_type: "production" }] });
    } else {
      await route.fulfill({ json: [] });
    }
  });

  await page.goto("/deployments");
  await expect(releaseRows(page)).toHaveCount(25);
  await expect(page.getByRole("button", { name: "Retry release", exact: true }).first()).toBeVisible();
  const nextRequest = page.waitForRequest((request) => new URL(request.url()).pathname === `/api/v1/workspaces/${nextWorkspaceId}/deployments`);
  try {
    await page.getByRole("combobox", { name: "Workspace", exact: true }).selectOption(nextWorkspaceId);
    await nextRequest;
    await expect(page.getByText("Loading live data…", { exact: true })).toBeVisible();
    await expect(releaseRows(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Retry release", exact: true })).toHaveCount(0);
    await expect(page.getByText("v-release-01 · main", { exact: true })).toHaveCount(0);
  } finally {
    resolveNextReleases();
  }

  await expect(releaseRows(page)).toHaveCount(1);
  await expect(releaseRows(page).first()).toContainText("Payments API");
  await expect(releaseRows(page).first()).toContainText("v-next-workspace");
  await expect(page.getByRole("button", { name: "Cancel release", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry release", exact: true })).toHaveCount(0);
});
