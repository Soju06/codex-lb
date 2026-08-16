import { expect, test } from "@playwright/test";

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
  const consentDecision = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/settings/telemetry" && response.request().method() === "PUT",
  );
  await consentDialog.getByRole("button", { name: "Keep enabled" }).click();
  expect((await consentDecision).ok()).toBe(true);
  await expect(consentDialog).toBeHidden();

  await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
  await expect(page.getByText("No accounts connected yet", { exact: true })).toBeVisible();
  await expect(page.getByText("No requests yet", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);

  await page.waitForLoadState("networkidle");
  expect(apiFailures).toEqual([]);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
