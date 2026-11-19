import { expect, test, type Page } from "@playwright/test";

type LoginPayload = { email: string; password: string };
type AuthOptions = {
  oidcEnabled?: boolean;
  localEnabled?: boolean;
  platformAdmin?: boolean;
  rejectLogin?: boolean;
  pendingLogin?: Promise<void>;
};

const emailAddress = "engineer@example.test";
const passwordValue = "  correct horse battery staple  ";
const session = {
  user: {
    id: "password-login-engineer",
    email: emailAddress,
    display_name: "Platform Engineer",
    email_verified: false,
    is_platform_admin: true,
    active: true,
    created_at: "2026-11-06T08:00:00Z",
  },
  memberships: [],
  csrf_token: "csrf-password-login",
};

async function mockAuthentication(page: Page, options: AuthOptions = {}) {
  let authenticated = false;
  const submissions: LoginPayload[] = [];
  const authSession = { ...session, user: { ...session.user, is_platform_admin: options.platformAdmin ?? true } };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api\/v1/, "");
    if (path === "/auth/me") {
      await route.fulfill(authenticated
        ? { json: authSession }
        : { status: 401, json: { error: { message: "Authentication required." } } });
    } else if (path === "/auth/config") {
      await route.fulfill({ json: {
        oidc_enabled: options.oidcEnabled ?? false,
        development_login_enabled: options.localEnabled ?? true,
        authentication_required: true,
      } });
    } else if (path === "/auth/password-login") {
      submissions.push(route.request().postDataJSON() as LoginPayload);
      if (options.pendingLogin) await options.pendingLogin;
      if (options.rejectLogin) {
        await route.fulfill({ status: 401, json: { error: { message: "Invalid email or password." } } });
      } else {
        authenticated = true;
        await route.fulfill({ json: authSession });
      }
    } else if (path === "/workspaces") {
      await route.fulfill({ json: [] });
    } else if (path === "/system/status") {
      await route.fulfill({ json: {
        api: "healthy", database: "healthy", worker: "healthy",
        worker_last_seen_at: "2026-11-06T08:00:00Z", checked_at: "2026-11-06T08:00:00Z",
      } });
    } else {
      await route.fulfill({ status: 404, json: { error: { message: `Unexpected test API request: ${path}` } } });
    }
  });
  return { submissions };
}

for (const viewport of [
  { name: "desktop", width: 1440, height: 1050 },
  { name: "mobile", width: 390, height: 844 },
  { name: "mobile landscape", width: 667, height: 375 },
]) {
  test(`email and password sign-in is accessible without horizontal overflow on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockAuthentication(page);
    await page.goto("/overview");

    const email = page.getByLabel("Email address", { exact: true });
    const password = page.getByLabel("Password", { exact: true });
    await expect(page.getByRole("heading", { name: "Sign in to your platform", exact: true })).toBeVisible();
    await expect(email).toBeVisible();
    await expect(email).toHaveAttribute("type", "email");
    await expect(email).toHaveAttribute("autocomplete", "username");
    await expect(password).toHaveAttribute("type", "password");
    await expect(password).toHaveAttribute("autocomplete", "current-password");
    await expect(page.getByLabel("Display name", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();

    await password.fill("password-visibility-check");
    const showPassword = page.getByRole("button", { name: "Show password", exact: true });
    await showPassword.focus();
    await page.keyboard.press("Enter");
    await expect(password).toHaveAttribute("type", "text");
    await expect(password).toHaveValue("password-visibility-check");
    await expect(page.getByRole("heading", { name: "Platform overview", exact: true })).toHaveCount(0);
    const hidePassword = page.getByRole("button", { name: "Hide password", exact: true });
    await hidePassword.focus();
    await page.keyboard.press("Space");
    await expect(password).toHaveAttribute("type", "password");
    await password.fill("");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    if (process.env.CAPTURE_AUTH_SCREENSHOTS === "1") {
      await page.screenshot({ path: `/tmp/deployforge-qa/login-${viewport.name}.png`, fullPage: true });
    }
  });
}

test("sign-in trims the email, preserves the exact password, and restores the resulting session after reload", async ({ page }) => {
  const auth = await mockAuthentication(page);
  await page.goto("/overview");
  await page.getByLabel("Email address", { exact: true }).fill(`  ${emailAddress}  `);
  await page.getByLabel("Password", { exact: true }).fill(passwordValue);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Platform overview", exact: true })).toBeVisible();
  expect(auth.submissions).toEqual([{ email: emailAddress, password: passwordValue }]);
  expect(await page.evaluate((password) => JSON.stringify(window.localStorage).includes(password), passwordValue)).toBe(false);

  await page.reload();
  await expect(page.getByRole("heading", { name: "Platform overview", exact: true })).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toHaveCount(0);
  expect(auth.submissions).toHaveLength(1);
});

test("incorrect credentials show an alert and keep authenticated content closed", async ({ page }) => {
  const auth = await mockAuthentication(page, { rejectLogin: true });
  await page.goto("/overview");
  await page.getByLabel("Email address", { exact: true }).fill(emailAddress);
  await page.getByLabel("Password", { exact: true }).fill("wrong-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("Invalid email or password.");
  await expect(page.getByRole("heading", { name: "Platform overview", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Sign in to your platform", exact: true })).toBeVisible();
  await expect(page.getByLabel("Email address", { exact: true })).toHaveValue(emailAddress);
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeEnabled();
  expect(auth.submissions).toHaveLength(1);
});

test("a collapsed sidebar clears a previous account's workspace before loading the new account", async ({ page }) => {
  const foreignWorkspaceId = "another-accounts-workspace";
  await page.addInitScript((workspaceId) => {
    localStorage.setItem("deployforge.sidebar-collapsed", "true");
    localStorage.setItem("deployforge.workspace", workspaceId);
    localStorage.setItem("deployforge.environment", "another-accounts-environment");
  }, foreignWorkspaceId);
  const foreignWorkspaceRequests: string[] = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.includes(`/workspaces/${foreignWorkspaceId}/`)) foreignWorkspaceRequests.push(pathname);
  });
  await mockAuthentication(page, { platformAdmin: false });
  await page.goto("/overview");
  await page.getByLabel("Email address", { exact: true }).fill(emailAddress);
  await page.getByLabel("Password", { exact: true }).fill(passwordValue);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();

  await expect(page.getByRole("heading", { name: "Platform overview", exact: true })).toBeVisible();
  await expect(page.getByText("Select a workspace", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => ({
    workspace: localStorage.getItem("deployforge.workspace"),
    environment: localStorage.getItem("deployforge.environment"),
  }))).toEqual({ workspace: "", environment: null });
  expect(foreignWorkspaceRequests).toEqual([]);
  await expect(page.locator(".app-shell")).toHaveClass(/sidebar-collapsed/);
  await page.getByRole("button", { name: "Expand sidebar", exact: true }).click();
  await expect(page.locator(".app-shell")).not.toHaveClass(/sidebar-collapsed/);
  await expect(page.getByRole("combobox", { name: "Workspace", exact: true })).toBeDisabled();
  await expect(page.getByRole("combobox", { name: "Workspace", exact: true })).toContainText("No workspace configured");
});

test("both credentials are required before any sign-in request is sent", async ({ page }) => {
  const auth = await mockAuthentication(page);
  await page.goto("/overview");
  const email = page.getByLabel("Email address", { exact: true });
  const password = page.getByLabel("Password", { exact: true });
  const submit = page.getByRole("button", { name: "Sign in", exact: true });
  await expect(email).toHaveAttribute("required", "");
  await expect(password).toHaveAttribute("required", "");

  await submit.click();
  await expect(email).toBeFocused();
  expect(auth.submissions).toHaveLength(0);
  await email.fill(emailAddress);
  await submit.click();
  await expect(password).toBeFocused();
  expect(auth.submissions).toHaveLength(0);
  await email.fill("");
  await password.fill("only-password");
  await submit.click();
  await expect(email).toBeFocused();
  expect(auth.submissions).toHaveLength(0);
});

test("an in-flight sign-in cannot be submitted twice", async ({ page }) => {
  let completeLogin = () => {};
  const pendingLogin = new Promise<void>((resolve) => { completeLogin = resolve; });
  const auth = await mockAuthentication(page, { pendingLogin });
  await page.goto("/overview");
  await page.getByLabel("Email address", { exact: true }).fill(emailAddress);
  await page.getByLabel("Password", { exact: true }).fill(passwordValue);
  try {
    await page.locator("form.dev-login-form").evaluate((form: HTMLFormElement) => {
      form.requestSubmit();
      form.requestSubmit();
    });
    await expect.poll(() => auth.submissions.length).toBe(1);
    await expect(page.getByRole("button", { name: "Signing in…", exact: true })).toBeDisabled();
    await expect(page.getByRole("heading", { name: "Platform overview", exact: true })).toHaveCount(0);
  } finally {
    completeLogin();
  }
  await expect(page.getByRole("heading", { name: "Platform overview", exact: true })).toBeVisible();
  expect(auth.submissions).toHaveLength(1);
});

test("organization SSO can be offered alongside email and password", async ({ page }) => {
  await mockAuthentication(page, { oidcEnabled: true });
  await page.goto("/overview");
  await expect(page.getByRole("button", { name: "Continue with organization SSO", exact: true })).toBeVisible();
  await expect(page.getByLabel("Email address", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
});

test("SSO-only configuration does not expose a disabled local sign-in form", async ({ page }) => {
  await mockAuthentication(page, { oidcEnabled: true, localEnabled: false });
  await page.goto("/overview");
  await expect(page.getByRole("button", { name: "Continue with organization SSO", exact: true })).toBeVisible();
  await expect(page.getByLabel("Email address", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Password", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toHaveCount(0);
});

test("a configuration with no sign-in method explains that access is unavailable", async ({ page }) => {
  await mockAuthentication(page, { localEnabled: false });
  await page.goto("/overview");
  await expect(page.getByText("No sign-in method is available. Configure the identity provider before opening the control plane.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Email address", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Password", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Platform overview", exact: true })).toHaveCount(0);
});
