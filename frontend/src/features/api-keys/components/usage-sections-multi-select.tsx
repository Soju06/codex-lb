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
import { USAGE_SECTION_LABELS, USAGE_SECTIONS, type UsageSection } from "@/features/api-keys/schemas";

export type UsageSectionsMultiSelectProps = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
};

function parseSections(raw: string): Set<UsageSection> {
  const trimmed = raw.trim();
  if (trimmed === "") return new Set();

  const sections = trimmed
    .split(",")
    .map((s) => s.trim())
    .filter((s): s is UsageSection => USAGE_SECTIONS.includes(s as UsageSection));
  if (sections.length === 0) return new Set(USAGE_SECTIONS);
  return new Set(sections);
}

function formatSections(raw: string, allSectionsLabel: string, t: ReturnType<typeof useTranslation>["t"]): string {
  const sections = parseSections(raw);
  if (sections.size === USAGE_SECTIONS.length) return allSectionsLabel;
  if (sections.size === 0) return t("common.options.none");
  return USAGE_SECTIONS.filter((s) => sections.has(s))
    .map((s) => t(`apiKeys.usageSections.${s}`, { defaultValue: USAGE_SECTION_LABELS[s] }))
    .join(", ");
}

export function UsageSectionsMultiSelect({
  value,
  onChange,
  placeholder,
}: UsageSectionsMultiSelectProps) {
  const { t } = useTranslation();
  const placeholderLabel = placeholder ?? t("apiKeys.usageSections.allSections");
  const selected = useMemo(() => parseSections(value), [value]);

  const toggle = useCallback(
    (section: UsageSection) => {
      const next = new Set(selected);
      if (next.has(section)) {
        next.delete(section);
      } else {
        next.add(section);
      }
      onChange(Array.from(next).join(","));
    },
    [onChange, selected],
  );

  const label = value.trim() === "" ? t("common.options.none") : formatSections(value, placeholderLabel, t);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="outline" className="w-full justify-between font-normal">
          <span className="truncate text-left">{label}</span>
          <ChevronsUpDown className="ml-1 size-4 shrink-0 opacity-50" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-[var(--radix-dropdown-menu-trigger-width)]">
        {USAGE_SECTIONS.map((section) => (
          <DropdownMenuCheckboxItem
            key={section}
            checked={selected.has(section)}
            onCheckedChange={() => toggle(section)}
            onSelect={(event) => event.preventDefault()}
          >
            {t(`apiKeys.usageSections.${section}`, { defaultValue: USAGE_SECTION_LABELS[section] })}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
