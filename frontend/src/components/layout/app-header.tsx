import { LogOut, Menu } from "lucide-react";
import { useState } from "react";
import { NavLink } from "react-router-dom";

import { CodexLogo } from "@/components/brand/codex-logo";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/accounts", label: "Accounts" },
  { to: "/settings", label: "Settings" },
] as const;

export type AppHeaderProps = {
  onLogout: () => void;
  showLogout?: boolean;
  className?: string;
};

export function AppHeader({
  onLogout,
  showLogout = true,
  className,
}: AppHeaderProps) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <header
      className={cn(
        "sticky top-0 z-20 border-b border-white/[0.08] bg-background/50 px-4 py-2.5 shadow-[0_1px_12px_rgba(0,0,0,0.06)] backdrop-blur-xl backdrop-saturate-[1.8] supports-[backdrop-filter]:bg-background/40 dark:shadow-[0_1px_12px_rgba(0,0,0,0.25)]",
        className,
      )}
    >
      <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4">
        {/* Brand */}
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-primary/15 to-primary/5">
            <CodexLogo size={20} className="text-primary" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold tracking-tight">Codex LB</p>
          </div>
        </div>

        {/* Desktop nav pills */}
        <nav className="hidden items-center rounded-lg border border-border/50 bg-muted/40 p-0.5 sm:flex">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to}>
              {({ isActive }) => (
                <span
                  className={cn(
                    "relative rounded-md px-3.5 py-1.5 text-xs font-medium transition-colors duration-200",
                    isActive
                      ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {item.label}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Actions */}
        <div className="flex flex-1 items-center justify-end gap-1.5">
          {showLogout && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={onLogout}
              className="press-scale hidden h-8 gap-1.5 rounded-lg text-xs text-muted-foreground hover:text-foreground sm:inline-flex"
            >
              <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
              Logout
            </Button>
          )}

          {/* Mobile menu */}
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild>
              <Button type="button" size="icon" variant="ghost" aria-label="Open menu" className="h-8 w-8 rounded-lg sm:hidden">
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
                {NAV_ITEMS.map((item) => (
                  <NavLink key={item.to} to={item.to} onClick={() => setMobileOpen(false)}>
                    {({ isActive }) => (
                      <span
                        className={cn(
                          "block w-full rounded-lg px-3 py-2 text-left text-sm font-medium transition-colors",
                          isActive
                            ? "bg-primary/10 text-primary"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground",
                        )}
                      >
                        {item.label}
                      </span>
                    )}
                  </NavLink>
                ))}
                {showLogout && (
                  <>
                    <div className="my-2 h-px bg-border" />
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-medium text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                      onClick={() => {
                        setMobileOpen(false);
                        onLogout();
                      }}
                    >
                      <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
                      Logout
                    </button>
                  </>
                )}
              </nav>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
  );
}
