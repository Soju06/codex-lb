import path from "node:path";
import { fileURLToPath } from "node:url";
import { test, type Page, type Route } from "@playwright/test";

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
  settings,
  unauthenticatedSession,
} from "./fixtures";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCREENSHOT_DIR = path.resolve(__dirname, "../../docs/screenshots");
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

async function interceptApi(page: Page, session: SessionOverride = authSession) {
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
    if (p === "/api/accounts") return fulfill(route, { accounts });
    const trendsMatch = p.match(/^\/api\/accounts\/([^/]+)\/trends$/);
    if (trendsMatch) {
      const trends = accountTrends[trendsMatch[1]];
      if (trends) return fulfill(route, trends);
      return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "account_not_found", message: "Account not found" } }) });
    }
    if (p === "/api/settings") return fulfill(route, settings);
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

  await page.goto(`http://localhost:4173${opts.route}`, { waitUntil: "networkidle" });

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
