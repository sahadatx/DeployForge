import { expect, test, type Locator, type Page, type Route } from "@playwright/test";

const workspaceId = "workspace-record-smoke";
const timestamp = "2026-11-04T10:00:00Z";
const revision = "f08cd18c17537b53d2f243676bca768332855ba5";
const repository = "https://github.com/platform-engineering/infrastructure-production-automation-and-runtime-deployment-configuration";

async function mockRecords(page: Page, onMutation?: (route: Route, path: string) => Promise<void>) {
  const mutations: string[] = [];
  const unexpectedRequests: string[] = [];
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.addInitScript((selectedWorkspace: string) => window.localStorage.setItem("deployforge.workspace", selectedWorkspace), workspaceId);

  const environment = {
    id: "environment-record-smoke", workspace_id: workspaceId, slug: "staging", name: "Staging", environment_type: "staging",
    cluster_name: "production-us-east-platform-cluster", namespace: "checkout-platform", protected: true, allow_destroy: false,
    quota_cpu_millis: 4000, quota_memory_mib: 8192, quota_pods: 20, quota_persistent_volume_claims: 4, quota_services: 10, created_at: timestamp,
  };
  const service = {
    id: "service-record-smoke", workspace_id: workspaceId, name: "Checkout API", slug: "checkout-api", description: "Checkout runtime",
    system: "commerce", owner_team_id: null, owner_team_name: null, repository_url: repository, documentation_url: null,
    lifecycle: "production", criticality: "tier_1", tags: [], metadata: {}, golden_path_id: null, golden_path_slug: null,
    created_by: "another-engineer", created_at: timestamp, updated_at: timestamp,
  };
  const target = {
    id: "gitops-target-record-smoke", workspace_id: workspaceId, service_id: service.id, environment_id: environment.id,
    application_name: "checkout-staging", expected_project: "commerce-platform", expected_cluster_name: environment.cluster_name,
    artifact_name: "checkout-image", enabled: true, created_at: timestamp, updated_at: timestamp,
  };
  const gitopsRun = {
    id: "gitops-run-record-smoke", workspace_id: workspaceId, deployment_id: "deployment-record-smoke", build_run_id: null,
    target_id: target.id, rollback_from_run_id: null, attempt_number: 1, action_kind: "deploy", application_name: target.application_name,
    desired_revision: revision, observed_revision: revision, artifact_uri: "oci://registry.example.test/commerce/checkout-api:v2.8.1",
    artifact_digest: `sha256:${"a".repeat(64)}`, status: "succeeded", sync_status: "Synced", health_status: "Healthy",
    operation_phase: "Succeeded", destination_name: environment.cluster_name, destination_server: "https://cluster.example.test",
    rollout_snapshot: null, rollout_updated_at: null, rollout_actions: [], events: [],
    attempt_count: 1, max_attempts: 3, callback_deadline_at: null, failure_code: null, failure_message: null,
    started_at: timestamp, finished_at: timestamp, created_at: timestamp, updated_at: timestamp,
  };
  const infrastructureTarget = {
    id: "infrastructure-target-record-smoke", workspace_id: workspaceId, environment_id: environment.id, repository_url: repository,
    source_ref: "main", working_directory: "environments/commerce/staging/runtime-cluster", tool: "terraform",
    backend_workspace: "checkout-staging", enabled: true, created_at: timestamp, updated_at: timestamp,
  };
  const infrastructureRun = {
    id: "infrastructure-run-record-smoke", workspace_id: workspaceId, environment_id: environment.id, target_id: infrastructureTarget.id,
    requested_by: "another-engineer", approved_by: null, attempt_number: 1, attempt_count: 1, max_attempts: 3,
    run_kind: "plan", active_operation: "plan", status: "awaiting_approval", source_ref: "main", working_directory: infrastructureTarget.working_directory,
    external_run_id: "provider-run-record-smoke", run_url: "https://provider.example.test/runs/provider-run-record-smoke",
    plan_digest: `sha256:${"b".repeat(64)}`, plan_artifact_uri: "s3://plan-evidence/workspaces/commerce/infrastructure/staging/saved-plan.json",
    plan_has_changes: true, resource_additions: 3, resource_changes: 1, resource_destroys: 0, quota_usage: { cpu_millis: 2000, memory_mib: 4096 },
    callback_deadline_at: null, failure_code: null, failure_message: null, started_at: timestamp, finished_at: timestamp,
    created_at: timestamp, updated_at: timestamp,
  };
  const linkedRelease = {
    id: "deployment-linked-smoke", workspace_id: workspaceId, service_id: service.id, environment_id: environment.id,
    version: "v-linked-release", source_ref: "main", status: "succeeded", requested_by: "another-engineer",
    approved_by: "record-engineer", approved_at: timestamp, attempt_count: 1, max_attempts: 3,
    started_at: timestamp, finished_at: timestamp, failure_code: null, failure_message: null, created_at: timestamp, updated_at: timestamp,
  };
  const slo = {
    id: "slo-record-smoke", workspace_id: workspaceId, service_id: service.id, environment_id: environment.id, slug: "checkout-availability",
    name: "Checkout availability", indicator: "availability", objective: 0.999, window_hours: 24, latency_threshold_ms: null,
    enabled: true, created_by: "another-engineer", created_at: timestamp, last_evaluated_at: timestamp,
  };
  const evaluation = {
    id: "slo-evaluation-record-smoke", workspace_id: workspaceId, slo_id: slo.id, status: "breaching", window_start: timestamp,
    window_end: timestamp, total_events: 10000, good_events: 9980, attainment: 0.998, error_budget_remaining: -1, burn_rate: 2, created_at: timestamp,
  };
  const alert = {
    id: "alert-record-smoke", workspace_id: workspaceId, slo_id: slo.id, evaluation_id: evaluation.id, status: "firing",
    summary: "Checkout availability is consuming its error budget", details: { attainment: 0.998, source: { exporter: "checkout-otel-collector" } },
    acknowledged_by: null, acknowledged_at: null, resolved_at: null, created_at: timestamp,
  };
  const source = {
    id: "source-record-smoke", workspace_id: workspaceId, service_id: service.id, environment_id: environment.id,
    name: "checkout-collector", token_prefix: "telemetry_prefix_only", enabled: true, created_by: "another-engineer",
    created_at: timestamp, last_seen_at: timestamp,
  };
  const policy = {
    id: "policy-record-smoke", workspace_id: workspaceId, slug: "production-guardrails", name: "Production deployment guardrails",
    description: "Require independent review for production changes.", enabled: true, enforcement_mode: "enforce", actions: ["deployment"],
    rules: [{ id: "production-target", name: "Protected production target", field: "environment.protected", operator: "equals", expected: false, effect: "require_approval" }],
    version: 2, created_by: "another-engineer", updated_by: "another-engineer", created_at: timestamp, updated_at: timestamp,
  };
  const decision = {
    id: "policy-decision-record-smoke", workspace_id: workspaceId, action: "deployment", subject_type: "deployment", subject_id: "deployment-record-smoke",
    decision: "require_approval", requires_approval: true, context: { environment: { protected: false } }, policy_snapshot: [],
    policy_snapshot_sha256: "c".repeat(64), results: [{ rule_id: "production-target", effect: "require_approval" }], created_at: timestamp,
  };
  const exception = {
    id: "policy-exception-record-smoke", workspace_id: workspaceId, policy_id: policy.id, policy_version: 2, rule_id: "production-target",
    service_id: service.id, environment_id: environment.id, reason: "Allow the bounded staging recovery exercise during the scheduled maintenance window.",
    status: "pending", requested_by: "another-engineer", reviewed_by: null, review_comment: null, expires_at: "2026-11-05T10:00:00Z",
    reviewed_at: null, created_at: timestamp,
  };
  const resources: Record<string, unknown> = {
    environments: [environment], services: { items: [service], total: 1, limit: 100, offset: 0 }, deployments: [], "build-runs": [],
    "gitops-targets": [target], "gitops-runs": [gitopsRun], [`gitops-runs/${gitopsRun.id}`]: gitopsRun,
    "infrastructure-targets": [infrastructureTarget], "infrastructure-runs": [infrastructureRun],
    slos: [slo], alerts: [alert], "telemetry-sources": [source], policies: [policy], "policy-evaluations": [decision],
    [`gitops-targets/${target.id}/status`]: { application_name: target.application_name, provider_status: "available", failure_code: null,
      project: target.expected_project, sync_status: "Synced", health_status: "Healthy", operation_phase: "Succeeded", revision,
      destination_name: environment.cluster_name, destination_server: "https://cluster.example.test" },
    [`slos/${slo.id}/current`]: evaluation,
    [`policies/${policy.id}/exceptions`]: [exception],
    [`deployments/${linkedRelease.id}`]: linkedRelease,
    [`deployments/${linkedRelease.id}/events`]: [],
    members: [],
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api\/v1/, "");
    if (request.method() !== "GET") {
      mutations.push(`${request.method()} ${path}`);
      if (onMutation) await onMutation(route, path);
      else await route.fulfill({ status: 405, json: { error: { message: "Smoke checks never perform mutations." } } });
    } else if (path === "/auth/me") {
      await route.fulfill({ json: { user: { id: "record-engineer", email: "engineer@example.test", display_name: "Platform Engineer",
        email_verified: true, is_platform_admin: true, active: true, created_at: timestamp },
        memberships: [{ workspace_id: workspaceId, role: "workspace_admin" }], csrf_token: "csrf-record-smoke" } });
    } else if (path === "/workspaces") {
      await route.fulfill({ json: [{ id: workspaceId, slug: "record-smoke", name: "Commerce Platform", description: null, created_at: timestamp }] });
    } else if (path === "/system/status") {
      await route.fulfill({ json: { api: "healthy", database: "healthy", worker: "healthy", worker_last_seen_at: timestamp, checked_at: timestamp } });
    } else if (path === "/auth/config") {
      await route.fulfill({ json: { oidc_enabled: true, development_login_enabled: false, authentication_required: true } });
    } else {
      const resource = path.replace(/^\/workspaces\/[^/]+\//, "");
      if (Object.hasOwn(resources, resource)) await route.fulfill({ json: path.startsWith(`/workspaces/${workspaceId}/`) ? resources[resource] : resource === "services" ? { items: [], total: 0, limit: 100, offset: 0 } : [] });
      else {
        unexpectedRequests.push(path);
        await route.fulfill({ status: 404, json: { error: { message: `Unexpected smoke API request: ${path}` } } });
      }
    }
  });
  return { mutations, unexpectedRequests, runtimeErrors };
}

async function expectNoHorizontalOverflow(page: Page, drawer?: Locator) {
  const pageSize = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
    overflowing: Array.from(document.querySelectorAll("main > *, .policy-layout, .policy-layout > *, .policy-rule, .policy-list > *, .resource-panel"))
      .map((element) => ({ className: element.className, right: Math.round(element.getBoundingClientRect().right), width: Math.round(element.getBoundingClientRect().width) }))
      .filter((element) => element.right > window.innerWidth + 1),
  }));
  expect(pageSize.width, `Page overflow on ${page.url()}: ${JSON.stringify(pageSize)}`).toBeLessThanOrEqual(pageSize.viewport + 1);
  if (drawer) {
    const drawerSize = await drawer.evaluate((element) => ({ width: element.scrollWidth, viewport: element.clientWidth }));
    expect(drawerSize.width, `Drawer overflow on ${page.url()}: ${JSON.stringify(drawerSize)}`).toBeLessThanOrEqual(drawerSize.viewport + 1);
  }
}

for (const viewport of [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "narrow", width: 390, height: 844 },
  { name: "mobile landscape", width: 667, height: 375 },
]) {
  test(`workspace record drawers render stored evidence without overflow on ${viewport.name}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    const platform = await mockRecords(page);
    const scenarios = [
      { path: "/gitops", heading: "GitOps", views: [
        { button: "View GitOps target checkout-staging", title: "checkout-staging", field: "Expected project" },
        { button: "View GitOps run checkout-staging", title: "checkout-staging · deploy", field: "Observed revision" },
      ] },
      { path: "/infrastructure", heading: "Infrastructure", views: [
        { button: "View infrastructure target for Staging", title: "Staging automation", field: "Working directory" },
        { button: "View infrastructure run infrastr", title: "Staging · plan", field: "Plan digest" },
      ] },
      { path: "/observability", heading: "Observability", views: [
        { button: "View Checkout availability", title: "Checkout availability", field: "Good events" },
        { button: "View Checkout availability is consuming its error budget", title: "Checkout availability is consuming its error budget", field: "Evaluation details" },
        { button: "View checkout-collector", title: "checkout-collector", field: "Token prefix" },
      ] },
      { path: "/environments", heading: "Environments", views: [{ button: "View Staging environment", title: "Staging", field: "Namespace" }] },
      { path: "/policies", heading: "Policies", views: [
        { button: "View deployment policy decision", title: "deployment decision", field: "Policy snapshot" },
        { button: "View production-target exception", title: "Exception · production-target", field: "Policy version" },
      ] },
    ];
    for (const scenario of scenarios) {
      await page.goto(scenario.path);
      await expect(page.getByRole("heading", { name: scenario.heading, exact: true })).toBeVisible();
      for (const [index, view] of scenario.views.entries()) {
        const trigger = page.getByRole("button", { name: view.button, exact: true });
        await trigger.click();
        const drawer = page.getByRole("dialog", { name: view.title, exact: true });
        await expect(drawer).toBeVisible();
        await expect(drawer.getByText(view.field, { exact: true })).toBeVisible();
        await expect(drawer.locator("input, select, textarea")).toHaveCount(0);
        if (index === 0) await page.screenshot({ path: testInfo.outputPath(`${scenario.heading.toLowerCase()}-${viewport.name}-record.png`) });
        await expectNoHorizontalOverflow(page, drawer);
        await drawer.getByRole("button", { name: "Close", exact: true }).click();
        await expect(drawer).toHaveCount(0);
        await expect(trigger).toBeFocused();
      }
    }
    expect(platform.runtimeErrors).toEqual([]);
    expect(platform.unexpectedRequests).toEqual([]);
    expect(platform.mutations).toEqual([]);
  });

  test(`create drawers protect and reset unfinished input on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const platform = await mockRecords(page);
    const forms = [
      { path: "/environments", trigger: "New environment", title: "Register an environment", field: "Name", value: "Recovery staging" },
      { path: "/gitops", trigger: "Register target", title: "Register an Argo CD target", field: "Application name", value: "checkout-recovery" },
      { path: "/infrastructure", trigger: "Register target", title: "Register infrastructure automation", field: "GitHub repository", value: repository },
      { path: "/observability", trigger: "Create SLO", title: "Define a service level objective", field: "Name", value: "Checkout recovery objective" },
      { path: "/policies", trigger: "New policy", title: "Create policy", field: "Policy name", value: "Recovery guardrails" },
      { path: "/settings", trigger: "Create workspace", title: "Create workspace", field: "Workspace name", value: "Recovery platform" },
    ];
    for (const form of forms) {
      await page.goto(form.path);
      const trigger = page.getByRole("button", { name: form.trigger, exact: true });
      await trigger.click();
      const drawer = page.getByRole("dialog", { name: form.title, exact: true });
      const input = drawer.getByRole("textbox", { name: form.field, exact: true });
      await input.fill(form.value);
      page.once("dialog", (confirmation) => void confirmation.dismiss());
      await drawer.getByRole("button", { name: "Cancel", exact: true }).click();
      await expect(drawer).toBeVisible();
      await expect(input).toHaveValue(form.value);
      page.once("dialog", (confirmation) => void confirmation.accept());
      await drawer.getByRole("button", { name: "Reset", exact: true }).click();
      await expect(input).toHaveValue("");
      await expect(drawer.getByRole("button", { name: "Reset", exact: true })).toBeDisabled();
      await expectNoHorizontalOverflow(page, drawer);
      await drawer.getByRole("button", { name: "Cancel", exact: true }).click();
      await expect(drawer).toHaveCount(0);
      await expect(trigger).toBeFocused();
      await trigger.click();
      await expect(input).toHaveValue("");
      await page.keyboard.press("Escape");
      await expect(drawer).toHaveCount(0);
    }
    expect(platform.runtimeErrors).toEqual([]);
    expect(platform.unexpectedRequests).toEqual([]);
    expect(platform.mutations).toEqual([]);
  });
}

test("release validation rejects whitespace and preserves the draft while trimming submitted values", async ({ page }) => {
  const submitted: Array<Record<string, unknown>> = [];
  const platform = await mockRecords(page, async (route) => {
    submitted.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({ status: 422, json: { error: { message: "Fixture release validation failed." } } });
  });
  await page.goto("/deployments");
  await page.getByRole("button", { name: "Request release", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "Request a release", exact: true });
  await expect(drawer.getByText("Required fields", { exact: false })).toBeVisible();
  await drawer.getByRole("combobox", { name: "Service", exact: true }).selectOption("service-record-smoke");
  await drawer.getByRole("combobox", { name: "Environment", exact: true }).selectOption("environment-record-smoke");
  const version = drawer.getByRole("textbox", { name: "Target release version", exact: true });
  await version.fill("   ");
  await drawer.getByRole("button", { name: "Queue release", exact: true }).click();
  await expect(version).toBeFocused();
  expect(await version.evaluate((input: HTMLInputElement) => input.validationMessage)).toContain("Enter a value");
  expect(submitted).toEqual([]);

  await version.fill("  v2.8.1  ");
  await drawer.getByRole("textbox", { name: /Source ref/ }).fill("   ");
  await drawer.getByRole("button", { name: "Queue release", exact: true }).click();
  await expect(drawer.getByRole("alert")).toHaveText("Fixture release validation failed.");
  expect(submitted).toEqual([{ service_id: "service-record-smoke", environment_id: "environment-record-smoke", version: "v2.8.1" }]);
  await expect(version).toHaveValue("  v2.8.1  ");
  expect(platform.runtimeErrors).toEqual([]);
});

test("editable slugs support long names and preserve deliberate overrides for environments and objectives", async ({ page }) => {
  const submitted: Array<{ path: string; body: Record<string, unknown> }> = [];
  const platform = await mockRecords(page, async (route, path) => {
    submitted.push({ path, body: route.request().postDataJSON() as Record<string, unknown> });
    await route.fulfill({ status: 422, json: { error: { message: "Fixture validation failed." } } });
  });
  await page.goto("/environments");
  await page.getByRole("button", { name: "New environment", exact: true }).click();
  const environmentDrawer = page.getByRole("dialog", { name: "Register an environment", exact: true });
  const environmentName = environmentDrawer.getByRole("textbox", { name: "Name", exact: true });
  const environmentSlug = environmentDrawer.getByRole("textbox", { name: "Environment slug", exact: true });
  await environmentName.fill("commerce-".repeat(12));
  expect((await environmentSlug.inputValue()).length).toBeLessThanOrEqual(80);
  expect(await environmentSlug.evaluate((input: HTMLInputElement) => input.checkValidity())).toBe(true);
  await environmentSlug.fill("staging-recovery");
  await environmentName.fill("  ঢাকা প্ল্যাটফর্ম  ");
  await expect(environmentSlug).toHaveValue("staging-recovery");
  await environmentDrawer.getByRole("button", { name: "Create environment", exact: true }).click();
  await expect(environmentDrawer.getByRole("alert")).toHaveText("Fixture validation failed.");
  expect(submitted[0].body).toMatchObject({ name: "ঢাকা প্ল্যাটফর্ম", slug: "staging-recovery" });

  await page.goto("/observability");
  await page.getByRole("button", { name: "Create SLO", exact: true }).click();
  const objectiveDrawer = page.getByRole("dialog", { name: "Define a service level objective", exact: true });
  const objectiveName = objectiveDrawer.getByRole("textbox", { name: "Name", exact: true });
  const objectiveSlug = objectiveDrawer.getByRole("textbox", { name: "Objective slug", exact: true });
  await objectiveName.fill("checkout-".repeat(15));
  expect((await objectiveSlug.inputValue()).length).toBeLessThanOrEqual(100);
  expect(await objectiveSlug.evaluate((input: HTMLInputElement) => input.checkValidity())).toBe(true);
  await objectiveSlug.fill("checkout-recovery");
  await objectiveName.fill("  Checkout recovery  ");
  await objectiveDrawer.getByRole("combobox", { name: "Service", exact: true }).selectOption("service-record-smoke");
  await objectiveDrawer.getByRole("spinbutton", { name: "Objective (%)", exact: true }).fill("0.5");
  await objectiveDrawer.getByRole("button", { name: "Create objective", exact: true }).click();
  await expect(objectiveDrawer.getByRole("alert")).toHaveText("Fixture validation failed.");
  expect(submitted[1].body).toMatchObject({ name: "Checkout recovery", slug: "checkout-recovery", objective: 0.005 });
  await expect(objectiveName).toHaveValue("  Checkout recovery  ");
  expect(platform.runtimeErrors).toEqual([]);
});

test("a failed source creation preserves an earlier one-time credential and workspace changes clear it", async ({ page }) => {
  let attempts = 0;
  const platform = await mockRecords(page, async (route) => {
    attempts += 1;
    if (attempts === 1) await route.fulfill({ json: { token: "telemetry_mock_one_time_credential" } });
    else await route.fulfill({ status: 503, json: { error: { message: "Fixture telemetry service unavailable." } } });
  });
  await page.goto("/observability");
  const trigger = page.getByRole("button", { name: "Telemetry source", exact: true });
  const drawer = page.getByRole("dialog", { name: "Register a telemetry source", exact: true });
  for (const name of ["checkout-new-collector", "checkout-failed-collector"]) {
    await trigger.click();
    await drawer.getByRole("textbox", { name: "Source name", exact: true }).fill(name);
    await drawer.getByRole("combobox", { name: "Service", exact: true }).selectOption("service-record-smoke");
    await drawer.getByRole("button", { name: "Create credential", exact: true }).click();
    if (name === "checkout-new-collector") await expect(drawer).toHaveCount(0);
    else await expect(drawer.getByRole("alert")).toHaveText("Fixture telemetry service unavailable.");
    await expect(page.locator(".credential-panel code")).toHaveText("telemetry_mock_one_time_credential");
  }
  await page.evaluate(() => {
    localStorage.setItem("deployforge.workspace", "another-workspace");
    window.dispatchEvent(new CustomEvent("deployforge:workspace-changed", { detail: "another-workspace" }));
  });
  await expect(drawer).toHaveCount(0);
  await expect(page.locator(".credential-panel")).toHaveCount(0);
  expect(platform.runtimeErrors).toEqual([]);
});

test("record links from search open their release or alert and consume the query once", async ({ page }) => {
  const platform = await mockRecords(page);
  await page.goto("/deployments?release=deployment-linked-smoke");
  const release = page.getByRole("dialog", { name: "Release v-linked-release", exact: true });
  await expect(release).toBeVisible();
  await expect(page).toHaveURL(/\/deployments$/);
  await release.getByRole("button", { name: "Close", exact: true }).click();
  await expect(release).toHaveCount(0);
  await page.goto("/observability?alert=alert-record-smoke");
  const alert = page.getByRole("dialog", { name: "Checkout availability is consuming its error budget", exact: true });
  await expect(alert).toBeVisible();
  await expect(page).toHaveURL(/\/observability$/);
  await alert.getByRole("button", { name: "Close", exact: true }).click();
  await expect(alert).toHaveCount(0);
  expect(platform.runtimeErrors).toEqual([]);
  expect(platform.unexpectedRequests).toEqual([]);
  expect(platform.mutations).toEqual([]);
});
