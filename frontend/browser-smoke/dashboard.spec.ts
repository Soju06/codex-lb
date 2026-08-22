import { expect, test, type Locator, type Page } from "@playwright/test";

import { AuthSessionSchema } from "../src/features/auth/schemas";
import { DashboardProjectionsSchema } from "../src/features/dashboard/schemas";

const REQUIRED_API_PATHS = [
  "/api/dashboard-auth/session",
  "/api/dashboard/overview",
  "/api/dashboard/projections",
  "/api/request-logs/options",
  "/api/request-logs",
  "/api/settings/telemetry",
] as const;

async function acceptTelemetryConsent(page: Page, consentDialog: Locator): Promise<void> {
  const consentDecision = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/settings/telemetry" && response.request().method() === "PUT",
  );
  await consentDialog.getByRole("button", { name: "Keep enabled" }).click();
  expect((await consentDecision).ok()).toBe(true);
  await expect(consentDialog).toBeHidden();
}

async function acceptTelemetryConsentIfShown(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  const consentDialog = page.getByRole("dialog", { name: "Anonymous telemetry" });
  if (await consentDialog.isVisible()) {
    await acceptTelemetryConsent(page, consentDialog);
  }
}

async function openLongSettingsPage(page: Page, scrollTop: number): Promise<void> {
  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await acceptTelemetryConsentIfShown(page);

  const advancedTrigger = page.getByRole("button", { name: "Show advanced settings" });
  await advancedTrigger.click();
  await expect(page.getByRole("heading", { name: "Firewall", exact: true })).toBeVisible();

  await page.evaluate((top) => window.scrollTo({ top, behavior: "instant" }), scrollTop);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(scrollTop);
}

test("the built dashboard accepts real backend responses", async ({ page }) => {
  const apiFailures: string[] = [];
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];

  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => {
    pageErrors.push(error.message);
  });
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/")) {
      apiFailures.push(`${request.method()} ${path}: ${request.failure()?.errorText ?? "request failed"}`);
    }
  });
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (!path.startsWith("/api/")) {
      return;
    }
    if (!response.ok()) {
      apiFailures.push(`${response.request().method()} ${path}: HTTP ${response.status()}`);
    }
  });

  // Intentionally do not register page.route handlers: every response must
  // come from the uvicorn/FastAPI process started by the smoke harness.
  const requiredResponsesPromise = Promise.all(
    REQUIRED_API_PATHS.map((requiredPath) =>
      page.waitForResponse((response) => new URL(response.url()).pathname === requiredPath),
    ),
  );
  await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
  const requiredResponses = await requiredResponsesPromise;

  for (const response of requiredResponses) {
    expect(response.ok(), `${response.request().method()} ${new URL(response.url()).pathname}`).toBe(true);
  }
  const sessionResponse = requiredResponses[0];
  AuthSessionSchema.parse(await sessionResponse.json());
  const projectionsResponse = requiredResponses.find(
    (response) => new URL(response.url()).pathname === "/api/dashboard/projections",
  );
  if (!projectionsResponse) {
    throw new Error("Dashboard projections response was not captured");
  }
  DashboardProjectionsSchema.parse(await projectionsResponse.json());

  // First run against an empty database resolves telemetry consent as
  // undecided/default, so the informed-consent dialog must appear before
  // anything else. Exercise it as a first-class scenario: verify the exact
  // transmitted envelope is rendered, then keep telemetry enabled to unblock
  // the dashboard underneath.
  const consentDialog = page.getByRole("dialog", { name: "Anonymous telemetry" });
  await expect(consentDialog).toBeVisible();
  await expect(consentDialog.getByText('"instance_id"').first()).toBeVisible();
  await acceptTelemetryConsent(page, consentDialog);

  await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
  await expect(page.getByText("No accounts connected yet", { exact: true })).toBeVisible();
  await expect(page.getByText("No requests yet", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);

  await page.waitForLoadState("networkidle");
  expect(apiFailures).toEqual([]);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

test("desktop route navigation resets new pages without overriding query, history, or hash scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openLongSettingsPage(page, 1400);

  const settingsHeadingTop = await page
    .getByRole("heading", { name: "Settings", exact: true })
    .evaluate((heading) => heading.getBoundingClientRect().top);
  expect(settingsHeadingTop).toBeLessThan(0);

  await page.getByRole("link", { name: "Dashboard", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  const dashboardHeading = page.getByRole("heading", { name: "Dashboard", exact: true });
  await expect(dashboardHeading).toBeInViewport();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);

  await page.evaluate(() => {
    document.body.style.minHeight = "3000px";
    window.scrollTo({ top: 700, behavior: "instant" });
  });
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(700);
  await page.getByRole("button", { name: "Request Logs", exact: true }).click();
  const conversationsItem = page.getByRole("menuitemradio", { name: "Conversations", exact: true });
  await expect(conversationsItem).toBeVisible();
  const queryScrollTop = await page.evaluate(() => window.scrollY);
  expect(queryScrollTop).toBeGreaterThan(0);
  await conversationsItem.click();
  await expect(page).toHaveURL(/\/dashboard\?view=conversations$/);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(queryScrollTop);

  await page.goBack();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(queryScrollTop);

  await page.goBack();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(1400);

  await page.goto("/firewall", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/settings\?advanced=1#firewall$/);
  const firewallHeading = page.getByRole("heading", { name: "Firewall", exact: true });
  await expect(firewallHeading).toBeInViewport();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
});

test("mobile top-level navigation opens the destination heading at the top", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openLongSettingsPage(page, 1400);

  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("link", { name: "Dashboard", exact: true }).click();

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeInViewport();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
});
