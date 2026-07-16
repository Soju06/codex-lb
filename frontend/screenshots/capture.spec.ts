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
import { createAccountSummary } from "../src/test/mocks/factories";

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

test("accounts — light", async ({ page }) => {
  await capture(page, { file: "accounts.jpg", theme: "light", route: "/accounts" });
});

test("accounts — dark", async ({ page }) => {
  await capture(page, { file: "accounts-dark.jpg", theme: "dark", route: "/accounts" });
});

test("accounts list keeps many rows in an internal scroll region", async ({ page }) => {
  const manyAccounts = Array.from({ length: 40 }, (_, index) =>
    createAccountSummary({
      accountId: `acc_overflow_${index}`,
      email: `overflow-${index}@example.com`,
      displayName: `Overflow Account ${index}`,
      planType: "plus",
      status: "active",
    }),
  );

  await applyTheme(page, "light");
  await interceptApi(page, authSession, manyAccounts);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript((css: string) => {
    const style = document.createElement("style");
    style.textContent = css;
    (document.head ?? document.documentElement).appendChild(style);
  }, DISABLE_ANIMATIONS_CSS);
  await page.setViewportSize({ width: 1440, height: 1200 });
  await page.goto(`${BASE_URL}/accounts`, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="account-list-scroll-region"]', { timeout: 10_000 });

  const scrollRegion = page.getByTestId("account-list-scroll-region");
  const listCard = page.getByTestId("accounts-list-card");
  const addAccountButton = page.getByRole("button", { name: "Add account" });
  const statusBar = page.locator("footer");

  await expect(addAccountButton).toBeVisible();
  const initialDimensions = await scrollRegion.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(initialDimensions.clientHeight).toBeGreaterThan(512);
  expect(initialDimensions.scrollHeight).toBeGreaterThan(initialDimensions.clientHeight);
  const listCardBox = await listCard.boundingBox();
  const scrollRegionBox = await scrollRegion.boundingBox();
  const statusBarBox = await statusBar.boundingBox();
  if (!listCardBox || !scrollRegionBox || !statusBarBox) {
    throw new Error("Accounts list card, scroll region, or status bar is not measurable");
  }
  const bottomGap = listCardBox.y + listCardBox.height - (scrollRegionBox.y + scrollRegionBox.height);
  expect(bottomGap).toBeLessThanOrEqual(18);
  expect(scrollRegionBox.y + scrollRegionBox.height).toBeLessThanOrEqual(statusBarBox.y - 8);
  expect(await scrollRegion.evaluate((element) => element.scrollTop)).toBe(0);

  const reachedBottom = await scrollRegion.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
    const lastRow = element.lastElementChild;
    if (!lastRow) {
      return { scrollTop: element.scrollTop, lastRowVisible: false };
    }
    const rowRect = lastRow.getBoundingClientRect();
    const regionRect = element.getBoundingClientRect();
    return {
      scrollTop: element.scrollTop,
      lastRowVisible: rowRect.top >= regionRect.top && rowRect.bottom <= regionRect.bottom,
    };
  });
  expect(reachedBottom.scrollTop).toBeGreaterThan(0);
  expect(reachedBottom.lastRowVisible).toBe(true);
  await expect(addAccountButton).toBeVisible();

  await scrollRegion.evaluate((element) => {
    element.scrollTop = 0;
  });
  await page.getByRole("button", { name: "Need help?" }).click();
  await expect(page.getByText("Windows OAuth Help")).toBeVisible();

  const helpOpenDimensions = await scrollRegion.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(helpOpenDimensions.clientHeight).toBeLessThan(initialDimensions.clientHeight);
  expect(helpOpenDimensions.scrollHeight).toBeGreaterThan(helpOpenDimensions.clientHeight);

  const helpOpenScrollRegionBox = await scrollRegion.boundingBox();
  const helpOpenStatusBarBox = await statusBar.boundingBox();
  if (!helpOpenScrollRegionBox || !helpOpenStatusBarBox) {
    throw new Error("Help-open account scroll region or status bar is not measurable");
  }
  expect(helpOpenScrollRegionBox.y + helpOpenScrollRegionBox.height).toBeLessThanOrEqual(
    helpOpenStatusBarBox.y - 8,
  );
  await expect(page.getByRole("button", { name: "Need help?" })).toBeVisible();
  await expect(addAccountButton).toBeVisible();

  const helpOpenReachedBottom = await scrollRegion.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
    const lastRow = element.lastElementChild;
    if (!lastRow) {
      return { scrollTop: element.scrollTop, lastRowVisible: false };
    }
    const rowRect = lastRow.getBoundingClientRect();
    const regionRect = element.getBoundingClientRect();
    return {
      scrollTop: element.scrollTop,
      lastRowVisible: rowRect.top >= regionRect.top && rowRect.bottom <= regionRect.bottom,
    };
  });
  expect(helpOpenReachedBottom.scrollTop).toBeGreaterThan(0);
  expect(helpOpenReachedBottom.lastRowVisible).toBe(true);
});

test("accounts list card ends after the final row when all accounts fit", async ({ page }) => {
  await applyTheme(page, "light");
  await interceptApi(page, authSession, accounts.slice(0, 4));
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript((css: string) => {
    const style = document.createElement("style");
    style.textContent = css;
    (document.head ?? document.documentElement).appendChild(style);
  }, DISABLE_ANIMATIONS_CSS);
  await page.setViewportSize({ width: 1440, height: 1200 });
  await page.goto(`${BASE_URL}/accounts`, { waitUntil: "networkidle" });

  const scrollRegion = page.getByTestId("account-list-scroll-region");
  const listCard = page.getByTestId("accounts-list-card");
  const dimensions = await scrollRegion.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(dimensions.scrollHeight).toBeLessThanOrEqual(dimensions.clientHeight);

  const listCardBox = await listCard.boundingBox();
  const scrollRegionBox = await scrollRegion.boundingBox();
  if (!listCardBox || !scrollRegionBox) {
    throw new Error("Accounts list card or scroll region is not measurable");
  }
  const bottomGap =
    listCardBox.y + listCardBox.height -
    (scrollRegionBox.y + scrollRegionBox.height);
  expect(bottomGap).toBeLessThanOrEqual(18);
  await expect(page.getByRole("button", { name: "Add account" })).toBeVisible();
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
