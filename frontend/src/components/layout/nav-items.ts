import type { Permission } from "@/features/auth/schemas";

export type NavItem = { to: string; labelKey: string; requires: Permission };

// Single source of truth for the header navigation. `requires` mirrors the
// permission the backend route matrix demands for the page's reads; items the
// session cannot use are filtered at render and their routes are guarded.
// Budgeted by .github/simplicity-budgets.toml: add metadata here, never items.
export const CORE_NAV_ITEMS = [
  { to: "/dashboard", labelKey: "nav.dashboard", requires: "dashboard:read" },
  { to: "/reports", labelKey: "nav.reports", requires: "dashboard:read" },
  { to: "/accounts", labelKey: "nav.accounts", requires: "accounts:read" },
  { to: "/apis", labelKey: "nav.apis", requires: "dashboard:read" },
  { to: "/settings", labelKey: "nav.settings", requires: "dashboard:read" },
] as const satisfies readonly NavItem[];

export const ADVANCED_NAV_ITEMS = [
  { to: "/automations", labelKey: "nav.automations", requires: "dashboard:read" },
] as const satisfies readonly NavItem[];

/** The permission a route needs, looked up from the nav definition (`null` for unlisted routes). */
export function routePermission(pathname: string): Permission | null {
  const item = [...CORE_NAV_ITEMS, ...ADVANCED_NAV_ITEMS].find(
    (candidate) => pathname === candidate.to || pathname.startsWith(`${candidate.to}/`),
  );
  return item?.requires ?? null;
}
