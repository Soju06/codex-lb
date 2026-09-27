import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page, type Route } from "@playwright/test";

import {
  accounts,
  accountTrends,
  apiKeys,
  authSession,
  createRequestLogsResponse,
  filterOptions,
  models,
  overview,
  requestLogs,
  resetCreditSnapshots,
  settings,
  upstreamProxyAdmin,
  unauthenticatedSession,
} from "./fixtures";
import {
  createAccountSummary,
  createConversationDetails,
  createConversationEntry,
  createConversationsResponse,
} from "../src/test/mocks/factories";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCREENSHOT_DIR = path.resolve(__dirname, "../../docs/screenshots");
const SCREENSHOT_PORT = process.env.SCREENSHOT_PORT ?? "4173";
const BASE_URL = process.env.SCREENSHOT_BASE_URL ?? `http://localhost:${SCREENSHOT_PORT}`;
const THEME_KEY = "codex-lb-theme";
const SETTLE_MS = 1500;

// CSS injected before page load to skip all animations/transitions instantly.
const DISABLE_ANIMATIONS_CSS = `
*, *::before, *::after {
  animation-duration: 0s !important;
  animation-delay: 0s !important;
  transition-duration: 0s !important;
  transition-delay: 0s !important;
}
`;

type Theme = "light" | "dark";
type SessionOverride = typeof authSession | typeof unauthenticatedSession;

// ── Route interception ──

function fulfill(route: Route, data: unknown) {
  return route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(data),
  });
}

async function interceptApi(
  page: Page,
  session: SessionOverride = authSession,
  accountList = accounts,
) {
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;

    if (p === "/api/dashboard-auth/session") return fulfill(route, session);
    if (p === "/api/dashboard/overview") return fulfill(route, overview);
    if (p === "/api/request-logs/options") return fulfill(route, filterOptions);
    if (p === "/api/request-logs") {
      const limit = Math.max(1, Number(url.searchParams.get("limit") ?? 50));
      const offset = Math.max(0, Number(url.searchParams.get("offset") ?? 0));
      const slice = requestLogs.slice(offset, offset + limit);
      return fulfill(route, createRequestLogsResponse(slice, requestLogs.length, offset + limit < requestLogs.length));
    }
    if (p === "/api/conversations") {
      return fulfill(
        route,
        createConversationsResponse([createConversationEntry({ conversationId: "conv_abc" })], 1, false),
      );
    }
    if (p === "/api/conversations/conv_abc") {
      return fulfill(route, createConversationDetails({ conversationId: "conv_abc" }));
    }
    if (p === "/api/accounts") return fulfill(route, { accounts: accountList });
    const trendsMatch = p.match(/^\/api\/accounts\/([^/]+)\/trends$/);
    if (trendsMatch) {
      const trends = accountTrends[trendsMatch[1]];
      if (trends) return fulfill(route, trends);
      return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "account_not_found", message: "Account not found" } }) });
    }
    if (p === "/api/settings") return fulfill(route, settings);
    if (p === "/api/settings/upstream-proxy") return fulfill(route, upstreamProxyAdmin);
    const usageResetCreditsMatch = p.match(/^\/api\/accounts\/([^/]+)\/usage-reset-credits$/);
    if (usageResetCreditsMatch) {
      const accountId = decodeURIComponent(usageResetCreditsMatch[1]);
      const snapshot = resetCreditSnapshots[accountId];
      return fulfill(route, {
        accountId,
        rateLimitResetCredits: { availableCount: snapshot?.availableCount ?? 0 },
      });
    }
    const resetCreditsMatch = p.match(/^\/api\/accounts\/([^/]+)\/rate-limit-reset-credits$/);
    if (resetCreditsMatch) {
      return fulfill(route, resetCreditSnapshots[decodeURIComponent(resetCreditsMatch[1])] ?? null);
    }
    if (p === "/api/models") return fulfill(route, { models });
    if (p === "/api/api-keys" || p === "/api/api-keys/") return fulfill(route, apiKeys);

    return route.abort();
  });

  await page.route("**/health", (route) => fulfill(route, { status: "ok" }));
}

// ── Theme ──

async function applyTheme(page: Page, theme: Theme) {
  await page.addInitScript(
    ({ key, value }: { key: string; value: string }) => {
      window.localStorage.setItem(key, value);
    },
    { key: THEME_KEY, value: theme },
  );
}

// ── Capture helper ──

async function capture(
  page: Page,
  opts: {
    file: string;
    theme: Theme;
    route: string;
    fullPage?: boolean;
    session?: SessionOverride;
    waitFor?: string;
    beforeScreenshot?: (page: Page) => Promise<void>;
  },
) {
  await applyTheme(page, opts.theme);
  await interceptApi(page, opts.session);

  // Trigger prefers-reduced-motion so the existing CSS media query kicks in.
  await page.emulateMedia({ reducedMotion: "reduce" });
  // Inject blanket CSS before page scripts run to kill CSS animations instantly.
  // (addInitScript survives navigation; addStyleTag on about:blank does not.)
  await page.addInitScript((css: string) => {
    const style = document.createElement("style");
    style.textContent = css;
    (document.head ?? document.documentElement).appendChild(style);
  }, DISABLE_ANIMATIONS_CSS);

  await page.goto(`${BASE_URL}${opts.route}`, { waitUntil: "networkidle" });

  if (opts.waitFor) {
    await page.waitForSelector(opts.waitFor, { timeout: 10_000 });
  }

  // Short settle for JS-driven rendering (Recharts SVG mutations etc.)
  await page.waitForTimeout(SETTLE_MS);

  if (opts.beforeScreenshot) {
    await opts.beforeScreenshot(page);
  }

  // For fullPage captures, un-fix the sticky footer so it flows at the document bottom
  // instead of floating at the original viewport boundary.
  if (opts.fullPage) {
    await page.evaluate(() => {
      const footer = document.querySelector("footer");
      if (footer) footer.style.position = "relative";
      // Remove the bottom padding that was reserving space for the fixed footer
      const layout = document.querySelector("main")?.parentElement;
      if (layout) layout.style.paddingBottom = "0";
    });
  }

  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, opts.file),
    type: "jpeg",
    quality: 90,
    fullPage: opts.fullPage ?? false,
  });
}

// ── Scenes ──

test("dashboard — light", async ({ page }) => {
  await capture(page, { file: "dashboard.jpg", theme: "light", route: "/dashboard" });
});

test("dashboard — dark", async ({ page }) => {
  await capture(page, { file: "dashboard-dark.jpg", theme: "dark", route: "/dashboard" });
});

test("dashboard conversations — desktop", async ({ page }) => {
  await capture(page, {
    file: "dashboard-conversations.jpg",
    theme: "light",
    route: "/dashboard?view=conversations",
    waitFor: '[data-slot="table"]',
  });
});

test("dashboard conversations — narrow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await capture(page, {
    file: "dashboard-conversations-narrow.jpg",
    theme: "light",
    route: "/dashboard?view=conversations",
    waitFor: '[data-slot="table"]',
  });
});

test("dashboard conversation details dialog", async ({ page }) => {
  await capture(page, {
    file: "dashboard-conversation-details.jpg",
    theme: "light",
    route: "/dashboard?view=conversations",
    waitFor: '[data-slot="table"]',
    beforeScreenshot: async (currentPage) => {
      await currentPage.getByRole("button", { name: /view details/i }).click();
      await currentPage.getByRole("dialog").waitFor();
      await currentPage.getByTestId("conversation-details-information").waitFor();
    },
  });
});

test("accounts — light", async ({ page }) => {
  await capture(page, { file: "accounts.jpg", theme: "light", route: "/accounts" });
});

test("accounts — dark", async ({ page }) => {
  await capture(page, { file: "accounts-dark.jpg", theme: "dark", route: "/accounts" });
});

test("accounts list paginates and keeps the same page across views and details", async ({ page }) => {
  const manyAccounts = Array.from({ length: 40 }, (_, index) => createAccountSummary({
    accountId: `acc_page_${index}`, email: `account-${index}@example.com`,
    displayName: `Account ${String(index).padStart(2, "0")}`,
  }));
  await applyTheme(page, "light");
  await interceptApi(page, authSession, manyAccounts);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`${BASE_URL}/accounts`, { waitUntil: "networkidle" });
  await expect(page.getByTestId("accounts-inline-detail")).toBeVisible();
  const selector = page.getByTestId("account-list-scroll-region");
  const dimensions = await selector.evaluate((node) => ({ height: node.clientHeight, content: node.scrollHeight }));
  expect(dimensions.content).toBeGreaterThan(dimensions.height);
  expect(await selector.getByRole("button").count()).toBe(40);
  await page.getByRole("button", { name: "List view", exact: true }).click();
  await expect(page.getByTestId("account-list-overview-row")).toHaveCount(24);
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText("25–40 of 40 accounts")).toBeVisible();
  await expect(page.getByTestId("account-list-overview-row")).toHaveCount(16);
  await page.getByTestId("account-list-overview-row").first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByText("25–40 of 40 accounts")).toBeVisible();
  await page.getByRole("button", { name: "Grid view", exact: true }).click();
  await expect(page.getByTestId("account-grid-card")).toHaveCount(16);
  await expect(page.getByText("25–40 of 40 accounts")).toBeVisible();
  await page.getByPlaceholder("Search accounts...").fill("account-0@");
  await expect(page.getByTestId("account-grid-card")).toHaveCount(1);
  await expect(page.getByText("1–1 of 1 accounts")).toBeVisible();
  await page.getByRole("button", { name: "Need help?" }).click();
  await expect(page.getByText("Windows OAuth Help")).toBeVisible();
});

test("settings — light", async ({ page }) => {
  await capture(page, { file: "settings.jpg", theme: "light", route: "/settings", fullPage: true });
});

test("settings — dark", async ({ page }) => {
  await capture(page, { file: "settings-dark.jpg", theme: "dark", route: "/settings", fullPage: true });
});

test("login", async ({ page }) => {
  await capture(page, {
    file: "login.jpg",
    theme: "light",
    route: "/",
    session: unauthenticatedSession,
    waitFor: 'input[type="password"]',
  });
});

test("redeem one account keeps the list and shows pending reconciliation", async ({ page }) => {
  await applyTheme(page, "light");
  await interceptApi(page);
  let listRequests = 0;
  let consumes = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/accounts") listRequests += 1;
  });
  await page.route("**/api/accounts/acc_01/rate-limit-reset-credits/consume", async (route) => {
    consumes += 1;
    await fulfill(route, { code: "reset", outcome: "confirmed_reset", windowsReset: 2, redeemedAt: new Date().toISOString() });
  });
  await page.route("**/api/accounts/acc_01/summary", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    await fulfill(route, { ...accounts[0], availableResetCredits: 2, resetCreditFetchedAt: new Date().toISOString() });
  });
  await page.goto(`${BASE_URL}/accounts`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /alex.research@fastlab.io/ }).click();
  const before = listRequests;
  const reset = page.getByRole("button", { name: /^Reset \(3\)/ });
  await reset.scrollIntoViewIfNeeded();
  const selectedUrl = page.url();
  const listScroll = await page.getByTestId("account-list-scroll-region").evaluate((node) => node.scrollTop);
  await reset.click();
  await page.getByRole("button", { name: "Redeem credit", exact: true }).click();
  await expect(page.locator("button").filter({ hasText: "Reset pending…" })).toBeDisabled();
  await expect(page.getByRole("alertdialog")).toBeHidden();
  await expect(page.getByRole("button", { name: /^Reset \(2\)/ }).first()).toBeVisible();
  expect(listRequests).toBe(before);
  expect(consumes).toBe(1);
  expect(page.url()).toBe(selectedUrl);
  expect(await page.getByTestId("account-list-scroll-region").evaluate((node) => node.scrollTop)).toBe(listScroll);
});
