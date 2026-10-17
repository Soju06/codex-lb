import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Eye, EyeOff, LogIn, LogOut, Menu, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, useLocation } from "react-router-dom";

import { CodexLogo } from "@/components/brand/codex-logo";
import { AccountMenu } from "@/components/layout/account-menu";
import { LanguageToggle, LanguageToggleMobile } from "@/components/layout/language-toggle";
import { ADVANCED_NAV_ITEMS, CORE_NAV_ITEMS } from "@/components/layout/nav-items";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { listAccounts } from "@/features/accounts/api";
import { hasPermission, useAuthStore } from "@/features/auth/hooks/use-auth";
import { getSettings } from "@/features/settings/api";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { cn } from "@/lib/utils";

export type AppHeaderProps = {
  onLogout: () => void;
  onAdminLogin?: () => void;
  showAdminLogin?: boolean;
  showLogout?: boolean;
  className?: string;
};

export function AppHeader({
  onLogout,
  onAdminLogin,
  showAdminLogin = false,
  showLogout = true,
  className,
}: AppHeaderProps) {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const permissions = useAuthStore((state) => state.permissions);
  const tier = useAuthStore((state) => state.tier);
  const user = useAuthStore((state) => state.user);
  // A break-glass session can exist on an install the store still derives as
  // the individual tier, where there is no account chip to hang a badge on —
  // so the indicator is a sibling of the chip, not an item inside its menu.
  const breakGlassSession = useAuthStore((state) => state.breakGlassSession);
  const coreNavItems = CORE_NAV_ITEMS.filter((item) => hasPermission(permissions, item.requires));
  const advancedNavItems = ADVANCED_NAV_ITEMS.filter((item) => hasPermission(permissions, item.requires));
  // Individual installs keep today's Logout button; from the team tier the
  // signed-in account gets the avatar chip and its menu instead (desktop; the
  // mobile sheet keeps its Logout entry on every tier).
  const showAccountMenu = tier !== "individual" && user !== null;
  const advancedActive = advancedNavItems.some(
    (item) => pathname === item.to || pathname.startsWith(`${item.to}/`),
  );
  const blurred = usePrivacyStore((s) => s.blurred);
  const togglePrivacy = usePrivacyStore((s) => s.toggle);
  const PrivacyIcon = blurred ? EyeOff : Eye;
  const { data: accounts = [] } = useQuery({
    queryKey: ["accounts", "list"],
    queryFn: listAccounts,
    select: (data) => data.accounts,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    staleTime: 30_000,
  });
  const { data: settings } = useQuery({
    queryKey: ["settings", "detail"],
    queryFn: getSettings,
  });
  const showResetCreditBadges = settings?.showResetCreditBadges ?? true;
  const totalAvailableResetCredits = accounts.reduce(
    (total, account) => total + Math.max(0, account.availableResetCredits ?? 0),
    0,
  );
  const accountsResetBadge = !showResetCreditBadges
    ? null
    : totalAvailableResetCredits > 99
    ? "99+"
    : totalAvailableResetCredits > 0
      ? String(totalAvailableResetCredits)
      : null;
  const privacyLabel = blurred ? t("nav.showEmails") : t("nav.hideEmails");

  return (
    <header
      className={cn(
        "sticky top-0 z-20 border-b border-white/[0.08] bg-background/50 px-4 py-2.5 shadow-[0_1px_12px_rgba(0,0,0,0.06)] backdrop-blur-xl backdrop-saturate-[1.8] supports-[backdrop-filter]:bg-background/40 dark:shadow-[0_1px_12px_rgba(0,0,0,0.25)]",
        className,
      )}
    >
      <div className="mx-auto flex w-full max-w-[1500px] items-center justify-between gap-4">
        {/* Brand */}
        <Link
          to="/dashboard"
          className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg no-underline transition-colors hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-primary/15 to-primary/5">
            <CodexLogo size={20} className="text-primary" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold tracking-tight">Codex LB</p>
          </div>
        </Link>

        {/* Desktop nav pills */}
        <nav className="hidden items-center rounded-lg border border-border/50 bg-muted/40 p-0.5 sm:flex">
          {coreNavItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                cn(
                  "relative inline-flex h-7 items-center rounded-md px-3.5 text-xs leading-none font-medium transition-colors duration-200",
                  isActive
                    ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                    : "text-muted-foreground hover:text-foreground",
                )
              }
            >
              <span className="relative inline-flex items-center">
                {t(item.labelKey)}
                {item.to === "/accounts" && accountsResetBadge ? (
                  <span className="absolute -top-2 -right-4 z-10 grid h-4 min-w-[1rem] place-items-center rounded-full bg-primary px-1 text-[10px] font-medium text-primary-foreground">
                    {accountsResetBadge}
                  </span>
                ) : null}
              </span>
            </NavLink>
          ))}
          <DropdownMenu>
            <DropdownMenuTrigger
              data-active={advancedActive}
              className={cn(
                "relative inline-flex h-7 items-center gap-1 rounded-md px-3.5 text-xs leading-none font-medium transition-colors duration-200",
                advancedActive
                  ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t("nav.advanced")}
              <ChevronDown className="h-3 w-3" aria-hidden="true" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {advancedNavItems.map((item) => (
                <DropdownMenuItem key={item.to} asChild>
                  <NavLink to={item.to} className="cursor-pointer">
                    {t(item.labelKey)}
                  </NavLink>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </nav>

        {/* Actions */}
        <div className="flex min-w-0 flex-1 items-center justify-end gap-1.5">
          <LanguageToggle />
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={togglePrivacy}
            aria-label={privacyLabel}
            className="press-scale hidden h-8 w-8 rounded-lg text-muted-foreground hover:text-foreground sm:inline-flex"
          >
            <PrivacyIcon className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
          {breakGlassSession ? (
            <span
              data-testid="emergency-session-pill"
              title={t("nav.emergencySession.description")}
              className="hidden shrink-0 items-center gap-1 rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700 sm:inline-flex dark:text-amber-400"
            >
              <ShieldAlert className="h-3 w-3" aria-hidden="true" />
              {t("nav.emergencySession.label")}
            </span>
          ) : null}
          {showAccountMenu ? <AccountMenu /> : null}
          {showLogout && !showAccountMenu && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={onLogout}
              className="press-scale hidden h-8 gap-1.5 rounded-lg text-xs text-muted-foreground hover:text-foreground sm:inline-flex"
            >
              <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
              {t("common.logout")}
            </Button>
          )}
          {showAdminLogin && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={onAdminLogin}
              className="press-scale hidden h-8 gap-1.5 rounded-lg text-xs sm:inline-flex"
            >
              <LogIn className="h-3.5 w-3.5" aria-hidden="true" />
              {t("nav.adminSignIn")}
            </Button>
          )}

          {/* Mobile menu */}
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild>
              <Button type="button" size="icon" variant="ghost" aria-label={t("nav.openMenu")} className="h-8 w-8 rounded-lg sm:hidden">
                <Menu className="h-4 w-4" />
              </Button>
            </SheetTrigger>
            <SheetContent side="right" className="w-72">
              <SheetHeader>
                <SheetTitle className="flex items-center gap-2.5">
                  <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10">
                    <CodexLogo size={16} className="text-primary" />
                  </div>
                  <span className="text-sm font-semibold">Codex LB</span>
                </SheetTitle>
              </SheetHeader>
              <nav className="flex flex-col gap-0.5 px-4 pt-2">
                {breakGlassSession ? (
                  <span
                    data-testid="emergency-session-pill-mobile"
                    className="mb-2 flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs font-medium text-amber-700 dark:text-amber-400"
                  >
                    <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" />
                    {t("nav.emergencySession.label")}
                  </span>
                ) : null}
                {coreNavItems.map((item) => (
                  <NavLink key={item.to} to={item.to} onClick={() => setMobileOpen(false)}>
                    {({ isActive }) => (
                      <span
                        className={cn(
                          "relative block w-full rounded-lg px-3 py-2 text-left text-sm font-medium transition-colors",
                          isActive
                            ? "bg-primary/10 text-primary"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground",
                        )}
                      >
                        {t(item.labelKey)}
                        {item.to === "/accounts" && accountsResetBadge ? (
                          <span className="absolute right-2 top-1 z-10 grid h-5 min-w-[1.25rem] place-items-center rounded-full bg-primary px-1 text-[10px] font-medium text-primary-foreground">
                            {accountsResetBadge}
                          </span>
                        ) : null}
                      </span>
                    )}
                  </NavLink>
                ))}
                <div className="my-2 h-px bg-border" />
                <p className="px-3 pb-1 text-[11px] font-medium tracking-wider text-muted-foreground uppercase">
                  {t("nav.advanced")}
                </p>
                {advancedNavItems.map((item) => (
                  <NavLink key={item.to} to={item.to} onClick={() => setMobileOpen(false)}>
                    {({ isActive }) => (
                      <span
                        className={cn(
                          "relative block w-full rounded-lg px-3 py-2 text-left text-sm font-medium transition-colors",
                          isActive
                            ? "bg-primary/10 text-primary"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground",
                        )}
                      >
                        {t(item.labelKey)}
                      </span>
                    )}
                  </NavLink>
                ))}
                <div className="my-2 h-px bg-border" />
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  onClick={togglePrivacy}
                >
                  <PrivacyIcon className="h-3.5 w-3.5" aria-hidden="true" />
                  {privacyLabel}
                </button>
                <div className="my-2 h-px bg-border" />
                <LanguageToggleMobile />
                {showLogout && (
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-medium text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                    onClick={() => {
                      setMobileOpen(false);
                      onLogout();
                    }}
                  >
                    <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
                    {t("common.logout")}
                  </button>
                )}
                {showAdminLogin && (
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    onClick={() => {
                      setMobileOpen(false);
                      onAdminLogin?.();
                    }}
                  >
                    <LogIn className="h-3.5 w-3.5" aria-hidden="true" />
                    {t("nav.adminSignIn")}
                  </button>
                )}
              </nav>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
  );
}
