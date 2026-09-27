import { expect, test } from "@playwright/test";
import path from "node:path";

import {
  accounts,
  accountTrends,
  authSession,
  overview,
  settings,
  upstreamProxyAdmin,
} from "./fixtures";

const directory = process.env.ACCOUNT_GRID_SCREENSHOTS;
const sampleTime = Date.parse("2026-09-27T12:00:00Z");
const at = (minutes: number) =>
  new Date(sampleTime + minutes * 60_000).toISOString();
const sampleAccounts = accounts.map((account, index) => ({
  ...account,
  email: `account-${index + 1}@example.com`,
  alias: [
    "Personal Plus",
    "Research Pro",
    "Team workspace",
    "Build runner",
    "Backup Plus",
    "Design tools",
    "Development",
  ][index],
  displayName: [
    "Personal Plus",
    "Research Pro",
    "Team workspace",
    "Build runner",
    "Backup Plus",
    "Design tools",
    "Development",
  ][index],
  workspaceLabel: index === 2 ? "Product Team" : "Personal workspace",
  seatType: index === 2 ? "owner" : null,
  planType: index === 1 ? "pro" : index === 2 ? "team" : "plus",
  status: index === 4 ? "paused" : "active",
  routingPolicy:
    index === 0 ? "burn_first" : index === 4 ? "preserve" : "normal",
  limitWarmupEnabled: index < 3,
  securityWorkAuthorized: index === 1,
  usage: {
    primaryRemainingPercent: [86, 64, 94, 47, 100, 14, 82][index],
    secondaryRemainingPercent: [72, 48, 90, 28, 100, 3, 62][index],
  },
  resetAtPrimary: at(126 + index * 12),
  resetAtSecondary: at((3 + index) * 24 * 60),
  lastRefreshAt: at(-18 - index * 9),
  creditsBalance: index === 1 ? 250 : 0,
  requestUsage: {
    requestCount: 1248 - index * 150,
    totalTokens: 8_400_000 - index * 800_000,
    cachedInputTokens: 3_100_000 - index * 400_000,
    totalCostUsd: 18.42 - index * 2.15,
  },
  auth: {
    access: { expiresAt: at(540) },
    refresh: { state: "stored" },
    idToken: { state: "parsed" },
  },
  subscription: {
    activeUntil:
      index === 2
        ? null
        : index === 3
          ? at(32 * 60)
          : index === 5
            ? at(-2 * 24 * 60)
            : at((18 - index) * 24 * 60 + 8 * 60),
    lastCheckedAt: at(-180),
  },
}));

for (const mode of ["detail", "list", "grid"] as const) {
  test(`Accounts ${mode} overview fits desktop and mobile and opens account management`, async ({
    page,
  }) => {
    const managementRequests: string[] = [];
    if (mode !== "detail") {
      await page.addInitScript(
        (storedMode) =>
          localStorage.setItem("codex-lb-accounts-view-mode", storedMode),
        mode,
      );
    }
    await page.clock.setFixedTime(new Date(sampleTime));
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.route("**/api/**", (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (
        pathname.endsWith("/trends") ||
        pathname.endsWith("/usage-reset-credits")
      )
        managementRequests.push(pathname);
      const data =
        pathname === "/api/dashboard-auth/session"
          ? authSession
          : pathname === "/api/settings"
            ? settings
            : pathname === "/api/settings/upstream-proxy"
              ? upstreamProxyAdmin
              : pathname === "/api/dashboard/overview"
                ? overview
                : pathname === "/api/accounts"
                  ? { accounts: sampleAccounts }
                  : pathname.endsWith("/trends")
                    ? accountTrends[pathname.split("/")[3]]
                    : pathname.endsWith("/usage-reset-credits")
                      ? {
                          accountId: pathname.split("/")[3],
                          rateLimitResetCredits: { availableCount: 2 },
                        }
                      : null;
      return data ? route.fulfill({ json: data }) : route.abort();
    });
    const baseUrl =
      process.env.SCREENSHOT_BASE_URL ??
      `http://localhost:${process.env.SCREENSHOT_PORT ?? "4173"}`;
    await page.goto(`${baseUrl}/accounts`);
    const items =
      mode === "detail"
        ? page.getByTestId("account-list-scroll-region").getByRole("button")
        : page.getByTestId(
            mode === "grid" ? "account-grid-card" : "account-list-overview-row",
          );
    await expect(items).toHaveCount(sampleAccounts.length);
    await page.waitForLoadState("networkidle");
    if (mode === "detail") {
      await expect(page.getByTestId("accounts-inline-detail")).toBeVisible();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      expect(managementRequests.length).toBeGreaterThan(0);
      expect(managementRequests.every((url) => url.includes("/acc_01/"))).toBe(
        true,
      );
      await expect(
        page
          .getByTestId("accounts-inline-detail")
          .locator(".recharts-area-curve")
          .first(),
      ).toBeVisible();
    } else expect(managementRequests).toEqual([]);
    if (mode === "detail") {
      await expect(
        items.first().getByTestId("account-plan-remaining"),
      ).toHaveText("18d 08h");
      const status = await items
        .first()
        .getByText("Active", { exact: true })
        .boundingBox();
      const remaining = await items
        .first()
        .getByTestId("account-plan-remaining")
        .boundingBox();
      expect(
        status && remaining && remaining.y >= status.y + status.height,
      ).toBeTruthy();
    }
    if (mode === "list") {
      await expect(
        items.first().getByTestId("account-plan-remaining"),
      ).toHaveText("18d 08h");
      await expect(
        items
          .first()
          .getByText(
            /requests|tokens|Stored|Parsed|Token refreshed|Warm-up|Last checked|Recorded end date/i,
          ),
      ).toHaveCount(0);
      await expect(
        items.first().getByText("Reset (3)", { exact: true }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: "Plan: Not sorted", exact: true })
        .click();
      await expect(items.last()).toContainText("Team workspace");
      await page
        .getByRole("button", { name: "Plan: Ascending", exact: true })
        .click();
      await expect(items.first()).toContainText("Team workspace");
      await page
        .getByRole("button", { name: "Subscription: Not sorted", exact: true })
        .click();
      await expect(items.first()).toContainText("Design tools");
      await expect(items.last()).toContainText("Team workspace");
      await page
        .getByRole("button", { name: "Subscription: Ascending", exact: true })
        .click();
      await expect(items.first()).toContainText("Personal Plus");
      await expect(items.last()).toContainText("Team workspace");
      const weekly = page.getByRole("button", {
        name: "Quota 7d: Not sorted",
        exact: true,
      });
      await weekly.focus();
      await page.keyboard.press("Enter");
      await expect(items.first()).toContainText("Design tools");
      await page.keyboard.press("Enter");
      await expect(items.first()).toContainText("Backup Plus");
      await page
        .getByRole("button", { name: "Quota 5h: Not sorted", exact: true })
        .click();
      await expect(items.first()).toContainText("Design tools");
      await page
        .getByRole("button", { name: "Quota 5h: Ascending", exact: true })
        .click();
      await expect(items.first()).toContainText("Backup Plus");
      await expect(
        page.getByRole("combobox", { name: "Sort accounts" }),
      ).toHaveText("5h quota (highest remaining)");
    }
    for (const width of [1440, 1024, 768, 390]) {
      await page.setViewportSize({
        width,
        height:
          mode === "detail"
            ? width === 390
              ? 1050
              : 1500
            : width === 390
              ? 1050
              : 1000,
      });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
      // Compact selector badges intentionally extend 4px past their row.
      if (mode !== "detail")
        expect(
          await items.evaluateAll((elements) =>
            elements.every(
              (element) => element.scrollWidth <= element.clientWidth,
            ),
          ),
        ).toBe(true);
      if (mode === "list") {
        const heights = await items.evaluateAll((rows) =>
          rows.map((row) => row.getBoundingClientRect().height),
        );
        expect(Math.max(...heights)).toBeLessThanOrEqual(
          width >= 1024 ? 80 : 180,
        );
      }
      if (mode === "detail" && width >= 1024) {
        const left = await page
          .getByTestId("accounts-list-panel")
          .boundingBox();
        const right = await page
          .getByTestId("accounts-inline-detail")
          .boundingBox();
        expect(left && right && left.x + left.width <= right.x).toBeTruthy();
      }
      if (directory && (width === 1440 || width === 390)) {
        if (mode === "detail") {
          await expect(
            page
              .getByTestId("accounts-inline-detail")
              .locator(".recharts-area-curve")
              .first(),
          ).toBeVisible();
        }
        await page.screenshot({
          path: path.join(
            directory,
            `${mode}-${width === 1440 ? "desktop" : "mobile"}.png`,
          ),
        });
      }
    }
    if (mode === "list") {
      await page.getByRole("combobox", { name: "Sort accounts" }).click();
      await expect(
        page.getByRole("option", {
          name: "Subscription (soonest)",
          exact: true,
        }),
      ).toBeVisible();
      if (directory)
        await page.screenshot({
          path: path.join(directory, "list-mobile-sort-menu.png"),
        });
      await page
        .getByRole("option", { name: "Subscription (soonest)", exact: true })
        .click();
      await expect(items.first()).toContainText("Design tools");
      await page
        .getByRole("button", { name: "Grid view", exact: true })
        .click();
      await expect(page.getByTestId("account-grid-card").first()).toContainText(
        "Design tools",
      );
      await page
        .getByRole("button", { name: "List view", exact: true })
        .click();
      await expect(items.first()).toContainText("Design tools");
      await expect(
        page.getByRole("combobox", { name: "Sort accounts" }),
      ).toHaveText("Subscription (soonest)");
    }
    if (mode === "detail") {
      await items.nth(1).click();
      await expect(
        page
          .getByTestId("accounts-inline-detail")
          .getByRole("heading", { name: "Research Pro", exact: true }),
      ).toBeVisible();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await page.reload();
      await expect(
        page.getByRole("button", { name: "Detail view", exact: true }),
      ).toHaveAttribute("aria-pressed", "true");
      await expect(
        page
          .getByTestId("accounts-inline-detail")
          .getByRole("heading", { name: "Research Pro", exact: true }),
      ).toBeVisible();
      return;
    }
    if (mode === "grid")
      await page
        .getByRole("button", { name: "View details", exact: true })
        .first()
        .click();
    else {
      await items.first().focus();
      await page.keyboard.press("Enter");
    }
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    expect(
      await dialog.evaluate(
        (element) => element.scrollWidth <= element.clientWidth,
      ),
    ).toBe(true);
    await page.getByRole("button", { name: "Close", exact: true }).click();
    expect(new URL(page.url()).searchParams.has("selected")).toBe(false);
    managementRequests.length = 0;
    await page.reload();
    await expect(items).toHaveCount(sampleAccounts.length);
    await page.waitForLoadState("networkidle");
    expect(managementRequests).toEqual([]);
  });
}
