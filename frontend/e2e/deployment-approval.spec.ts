import { expect, test, type Page } from "@playwright/test";

const workspaceId = "workspace-test";
const requesterId = "user-requester";
const deploymentId = "deployment-test";

function deployment(requestedBy: string, status = "awaiting_approval") {
  return {
    id: deploymentId,
    workspace_id: workspaceId,
    service_id: "service-test",
    environment_id: "environment-test",
    version: "v1.2.3",
    source_ref: "main",
    status,
    requested_by: requestedBy,
    idempotency_key: null,
    attempt_count: 1,
    max_attempts: 3,
    started_at: "2026-10-25T10:00:00Z",
    finished_at: null,
    approved_by: null,
    approved_at: null,
    failure_code: null,
    failure_message: null,
    created_at: "2026-10-25T10:00:00Z",
    updated_at: "2026-10-25T10:00:00Z",
  };
}

async function mockPlatform(page: Page, requestedBy: string, status = "awaiting_approval") {
  await page.addInitScript((selectedWorkspaceId: string) => {
    window.localStorage.setItem("deployforge.workspace", selectedWorkspaceId);
  }, workspaceId);

  await page.route("**/api/v1/**", async (route) => {
    const requestUrl = new URL(route.request().url());
    const path = requestUrl.pathname.replace(/^\/api\/v1/, "");

    if (path === "/auth/me") {
      await route.fulfill({
        json: {
          user: {
            id: requesterId,
            email: "engineer@example.test",
            display_name: "Platform Engineer",
            email_verified: true,
            is_platform_admin: true,
            active: true,
            created_at: "2026-10-25T09:00:00Z",
          },
          memberships: [{ workspace_id: workspaceId, role: "workspace_admin" }],
          csrf_token: "csrf-test-token",
        },
      });
      return;
    }

    if (path === "/workspaces") {
      await route.fulfill({
        json: [{ id: workspaceId, slug: "test-platform", name: "Test Platform", description: null, created_at: "2026-10-25T09:00:00Z" }],
      });
      return;
    }

    if (path === `/workspaces/${workspaceId}/deployments`) {
      await route.fulfill({ json: [deployment(requestedBy, status)] });
      return;
    }

    if (path === `/workspaces/${workspaceId}/deployments/${deploymentId}/events`) {
      await route.fulfill({ json: [] });
      return;
    }

    if (path === `/workspaces/${workspaceId}/deployments/${deploymentId}/approval` && route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { decision: "approve" | "reject" };
      await route.fulfill({ json: deployment(requestedBy, body.decision === "approve" ? "approved" : "rejected") });
      return;
    }

    if (path === `/workspaces/${workspaceId}/services`) {
      await route.fulfill({
        json: {
          items: [{ id: "service-test", name: "DeployForge Demo Service", slug: "deployforge-demo-service" }],
          total: 1,
          limit: 100,
          offset: 0,
        },
      });
      return;
    }

    if (path === `/workspaces/${workspaceId}/environments`) {
      await route.fulfill({
        json: [{ id: "environment-test", name: "Staging", environment_type: "staging" }],
      });
      return;
    }

    if (path === `/workspaces/${workspaceId}/alerts` || path === `/workspaces/${workspaceId}/gitops-runs` || path === `/workspaces/${workspaceId}/build-runs`) {
      await route.fulfill({ json: [] });
      return;
    }

    await route.fulfill({ status: 404, json: { error: { message: `Unexpected test API request: ${path}` } } });
  });
}

test("a requester cannot approve their own pending release", async ({ page }) => {
  await mockPlatform(page, requesterId);
  await page.goto("/deployments");

  await expect(page.getByRole("heading", { name: "Deployments" })).toBeVisible();
  const releaseRow = page.getByRole("row").filter({ hasText: "v1.2.3" });
  await expect(releaseRow.getByRole("button", { name: "Approve release" })).toHaveCount(0);
  await expect(releaseRow.getByRole("button", { name: "Reject release" })).toHaveCount(0);
  await expect(releaseRow.getByText("Needs another approver")).toBeVisible();

  await releaseRow.getByRole("button").first().click();
  await expect(page.getByText("Independent approval required")).toBeVisible();
  await expect(page.getByText(/A different workspace approver must review it/)).toBeVisible();
});

test("an independent workspace approver can submit a decision for a pending release", async ({ page }) => {
  await mockPlatform(page, "another-user");
  await page.goto("/deployments");

  const releaseRow = page.getByRole("row").filter({ hasText: "v1.2.3" });
  await expect(releaseRow.getByRole("button", { name: "Approve release" })).toBeVisible();
  await expect(releaseRow.getByRole("button", { name: "Reject release" })).toBeVisible();
  await expect(releaseRow.getByText("Needs another approver")).toHaveCount(0);

  await releaseRow.getByRole("button", { name: "Approve release" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Release approved." })).toBeVisible();
});

test("post-build release approvals show their review actions", async ({ page }) => {
  await mockPlatform(page, "another-user", "awaiting_release_approval");
  await page.goto("/deployments");

  const releaseRow = page.getByRole("row").filter({ hasText: "v1.2.3" });
  const approvalsMetric = page.locator(".metric-card").filter({ hasText: "Pending approvals" });
  await expect(approvalsMetric.locator("strong")).toHaveText("1");
  await expect(releaseRow.getByText("awaiting release approval")).toBeVisible();
  await expect(releaseRow.getByRole("button", { name: "Approve release" })).toBeVisible();
  await expect(releaseRow.getByRole("button", { name: "Reject release" })).toBeVisible();
});

test("the requester cannot self-approve a post-build release", async ({ page }) => {
  await mockPlatform(page, requesterId, "awaiting_release_approval");
  await page.goto("/deployments");

  const releaseRow = page.getByRole("row").filter({ hasText: "v1.2.3" });
  await expect(releaseRow.getByRole("button", { name: "Approve release" })).toHaveCount(0);
  await expect(releaseRow.getByRole("button", { name: "Reject release" })).toHaveCount(0);
  await expect(releaseRow.getByText("Needs another approver")).toBeVisible();
});
