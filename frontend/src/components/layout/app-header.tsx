import { LogOut, Moon, Sun } from "lucide-react";
import { NavLink } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/accounts", label: "Accounts" },
  { to: "/settings", label: "Settings" },
] as const;

export type AppHeaderProps = {
  theme: "light" | "dark";
  onThemeToggle: () => void;
  onLogout: () => void;
  className?: string;
};

export function AppHeader({
  theme,
  onThemeToggle,
  onLogout,
  className,
}: AppHeaderProps) {
  return (
    <header
      className={cn(
        "sticky top-0 z-20 border-b bg-background/95 px-4 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/75",
        className,
      )}
    >
      <div className="mx-auto flex w-full max-w-7xl items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="h-8 w-8 shrink-0 rounded-md bg-primary" />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">Codex LB</p>
            <p className="truncate text-xs text-muted-foreground">Admin Dashboard</p>
          </div>
        </div>

        <nav className="hidden items-center gap-1 sm:flex">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to}>
              {({ isActive }) => (
                <Button
                  type="button"
                  variant={isActive ? "default" : "ghost"}
                  className="h-8 px-3 text-xs"
                >
                  {item.label}
                </Button>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="flex items-center gap-2">
          <Button type="button" size="icon" variant="outline" onClick={onThemeToggle}>
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={onLogout}>
            <LogOut className="mr-2 h-4 w-4" />
            Logout
          </Button>
        </div>
      </div>
    </header>
  );
}
