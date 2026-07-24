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
    <h2 className="text-[13px] font-medium uppercase tracking-wider text-muted-foreground">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="group inline-flex items-center gap-1 rounded-sm text-[13px] font-medium uppercase tracking-wider text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={label}
          >
            <span>{label}</span>
            <ChevronDown
              className="h-3.5 w-3.5 transition-transform motion-reduce:transform-none motion-reduce:transition-none group-data-[state=open]:rotate-180"
              aria-hidden="true"
            />
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
    </h2>
  );
}
