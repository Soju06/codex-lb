import { ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { DashboardView } from "@/features/dashboard/schemas";

export type DashboardViewSelectorProps = {
  value: DashboardView;
  onChange: (value: DashboardView) => void;
};

export function DashboardViewSelector({ value, onChange }: DashboardViewSelectorProps) {
  const { t } = useTranslation();
  const label = t(`dashboard.views.${value}`);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="group inline-flex h-8 items-center gap-1.5 rounded-md border border-transparent px-2 text-sm font-medium transition-colors hover:border-border hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={label}
        >
          <span>{label}</span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground transition-transform motion-reduce:transition-none motion-reduce:transform-none group-data-[state=open]:rotate-180" aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-44">
        <DropdownMenuRadioGroup value={value} onValueChange={(next) => onChange(next as DashboardView)}>
          <DropdownMenuRadioItem value="request-logs">
            <span>{t("dashboard.views.request-logs")}</span>
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="conversations">
            <span>{t("dashboard.views.conversations")}</span>
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
