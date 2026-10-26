import { LayoutDashboard, PieChart } from "lucide-react";
import { useTranslation } from "react-i18next";
import { NavLink } from "react-router-dom";

import { cn } from "@/lib/utils";

const TABS = [
  { to: "/dashboard", end: true, labelKey: "dashboard.tabs.dashboard", icon: LayoutDashboard },
  { to: "/dashboard/overview", end: false, labelKey: "dashboard.tabs.overview", icon: PieChart },
] as const;

export function DashboardViewTabs() {
  const { t } = useTranslation();

  return (
    <nav
      aria-label={t("dashboard.tabs.aria")}
      className="inline-flex h-8 items-center rounded-md border bg-background p-0.5"
    >
      {TABS.map((tab) => {
        const Icon = tab.icon;
        return (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) =>
              cn(
                "inline-flex h-6 items-center gap-1.5 rounded-[5px] px-2.5 text-xs font-medium text-muted-foreground transition-colors",
                isActive ? "bg-accent text-accent-foreground shadow-sm" : "hover:bg-accent/60 hover:text-accent-foreground",
              )
            }
          >
            <Icon className="h-3.5 w-3.5" aria-hidden="true" />
            {t(tab.labelKey)}
          </NavLink>
        );
      })}
    </nav>
  );
}
