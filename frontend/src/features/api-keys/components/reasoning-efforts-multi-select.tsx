import { useCallback, useMemo } from "react";
import { ChevronsUpDown } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  REASONING_EFFORTS,
  type ReasoningEffortType,
} from "@/features/api-keys/schemas";

export type ReasoningEffortsMultiSelectProps = {
  value: ReasoningEffortType[];
  onChange: (value: ReasoningEffortType[]) => void;
  disabled?: boolean;
};

export function ReasoningEffortsMultiSelect({
  value,
  onChange,
  disabled = false,
}: ReasoningEffortsMultiSelectProps) {
  const { t } = useTranslation();
  const selected = useMemo(() => new Set(value), [value]);

  const toggle = useCallback(
    (effort: ReasoningEffortType) => {
      const next = new Set(selected);
      if (next.has(effort)) {
        next.delete(effort);
      } else {
        next.add(effort);
      }
      onChange(REASONING_EFFORTS.filter((candidate) => next.has(candidate)));
    },
    [onChange, selected],
  );

  const label =
    value.length === 0
      ? t("apiKeys.reasoningEfforts.all")
      : t("apiKeys.reasoningEfforts.selected", { count: value.length });
  const fieldLabel = t("apiKeys.form.allowedReasoningEfforts");

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          className="w-full justify-between font-normal"
          aria-label={`${fieldLabel}: ${label}`}
          disabled={disabled}
        >
          <span className="truncate text-left">{label}</span>
          <ChevronsUpDown className="ml-1 size-4 shrink-0 opacity-50" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-[var(--radix-dropdown-menu-trigger-width)]"
      >
        {REASONING_EFFORTS.map((effort) => (
          <DropdownMenuCheckboxItem
            key={effort}
            checked={selected.has(effort)}
            onCheckedChange={() => toggle(effort)}
            onSelect={(event) => event.preventDefault()}
          >
            {t(`common.reasoning.${effort}`)}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
