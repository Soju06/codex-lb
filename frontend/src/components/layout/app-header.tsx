import { Eye, EyeOff, LogOut, Menu } from "lucide-react";
import { useState } from "react";
import { NavLink } from "react-router-dom";

import { AgentLbLogo } from "@/components/brand/agent-lb-logo";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/reports", label: "Reports" },
  { to: "/sessions", label: "Sessions" },
  { to: "/accounts", label: "Accounts" },
  { to: "/apis", label: "APIs" },
  { to: "/team", label: "Team" },
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
  const blurred = usePrivacyStore((s) => s.blurred);
  const togglePrivacy = usePrivacyStore((s) => s.toggle);
  const PrivacyIcon = blurred ? EyeOff : Eye;

  return (
    <header
      className={cn(
        "sticky top-0 z-20 border-b border-border bg-background px-4 py-2.5",
        className,
      )}
    >
      <div className="mx-auto flex w-full max-w-[1500px] items-center justify-between gap-4">
        {/* Brand */}
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          <AgentLbLogo size={20} className="text-foreground" />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold tracking-tight">
              Agent LB
            </p>
          </div>
        </div>

        {/* Desktop nav */}
        <nav className="hidden items-center gap-0.5 sm:flex">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                cn(
                  "relative inline-flex h-7 items-center rounded-md px-3.5 text-xs leading-none font-medium transition-colors duration-150 ease-out",
                  isActive
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* Actions */}
        <div className="flex flex-1 items-center justify-end gap-1.5">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={togglePrivacy}
            aria-label={blurred ? "Show emails" : "Hide emails"}
            className="press-scale hidden h-8 w-8 rounded-lg text-muted-foreground hover:text-foreground sm:inline-flex"
          >
            <PrivacyIcon className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
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
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label="Open menu"
                className="h-8 w-8 rounded-lg sm:hidden"
              >
                <Menu className="h-4 w-4" />
              </Button>
            </SheetTrigger>
            <SheetContent side="right" className="w-72">
              <SheetHeader>
                <SheetTitle className="flex items-center gap-2.5">
                  <AgentLbLogo size={16} className="text-foreground" />
                  <span className="text-sm font-semibold">Agent LB</span>
                </SheetTitle>
              </SheetHeader>
              <nav className="flex flex-col gap-0.5 px-4 pt-2">
                {NAV_ITEMS.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    onClick={() => setMobileOpen(false)}
                  >
                    {({ isActive }) => (
                      <span
                        className={cn(
                          "block w-full rounded-lg px-3 py-2 text-left text-sm font-medium transition-colors duration-150 ease-out",
                          isActive
                            ? "bg-accent text-foreground"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground",
                        )}
                      >
                        {item.label}
                      </span>
                    )}
                  </NavLink>
                ))}
                <div className="my-2 h-px bg-border" />
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-medium text-muted-foreground transition-colors duration-150 ease-out hover:bg-muted hover:text-foreground"
                  onClick={togglePrivacy}
                >
                  <PrivacyIcon className="h-3.5 w-3.5" aria-hidden="true" />
                  {blurred ? "Show Emails" : "Hide Emails"}
                </button>
                {showLogout && (
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-medium text-muted-foreground transition-colors duration-150 ease-out hover:bg-muted hover:text-foreground"
                    onClick={() => {
                      setMobileOpen(false);
                      onLogout();
                    }}
                  >
                    <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
                    Logout
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
