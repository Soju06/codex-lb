import { ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

export type AdvancedSettingsGroupProps = {
  children: ReactNode;
};

/**
 * Collapsed-by-default container for power-user settings sections.
 *
 * Children are unmounted while the group is closed, so section data queries
 * only fire once the operator expands the group.
 */
export function AdvancedSettingsGroup({ children }: AdvancedSettingsGroupProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-xl border bg-card">
      <CollapsibleTrigger
        aria-label={open ? t("settings.advanced.hide") : t("settings.advanced.show")}
        className="flex w-full items-center gap-3 rounded-xl p-5 text-left transition-colors hover:bg-muted/40"
      >
        <ChevronRight
          aria-hidden="true"
          className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200", open && "rotate-90")}
        />
        <span className="min-w-0">
          <span className="block text-sm font-semibold tracking-tight">{t("settings.advanced.title")}</span>
          <span className="mt-0.5 block text-xs text-muted-foreground">{t("settings.advanced.description")}</span>
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-4 border-t p-4">{children}</CollapsibleContent>
    </Collapsible>
  );
}
