import { ChevronDown, X } from "lucide-react";
import { useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";

export type MultiSelectOption = {
  value: string;
  label: string;
};

type RenderedOption = MultiSelectOption & {
  isStale: boolean;
};

export type MultiSelectFilterProps = {
  label: string;
  values: string[];
  options: MultiSelectOption[];
  onChange: (values: string[]) => void;
};

export function MultiSelectFilter({ label, values, options, onChange }: MultiSelectFilterProps) {
  const renderedOptions = useMemo<RenderedOption[]>(() => {
    const byValue = new Map<string, RenderedOption>();
    for (const option of options) {
      byValue.set(option.value, { ...option, isStale: false });
    }
    for (const value of values) {
      if (!byValue.has(value)) {
        byValue.set(value, {
          value,
          label: value,
          isStale: true,
        });
      }
    }
    return [...byValue.values()];
  }, [options, values]);

  const labelByValue = useMemo(() => {
    const pairs = renderedOptions.map((option) => [option.value, option.label] as const);
    return new Map<string, string>(pairs);
  }, [renderedOptions]);

  const toggleValue = (value: string) => {
    if (values.includes(value)) {
      onChange(values.filter((entry) => entry !== value));
      return;
    }
    onChange([...values, value]);
  };

  const removeValue = (value: string) => {
    onChange(values.filter((entry) => entry !== value));
  };

  const summary =
    values.length === 0
      ? label
      : values.length === 1
        ? labelByValue.get(values[0]) ?? values[0]
        : `${label} (${values.length})`;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="outline" size="sm" className="min-w-[7rem] justify-between gap-1.5">
          {summary}
          <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-50" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 min-w-56 overflow-y-auto">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {renderedOptions.length === 0 ? (
          <p className="px-2 py-1 text-xs text-muted-foreground">No options</p>
        ) : (
          renderedOptions.map((option) => (
            <DropdownMenuCheckboxItem
              key={option.value}
              checked={values.includes(option.value)}
              onCheckedChange={() => toggleValue(option.value)}
              onSelect={(e) => e.preventDefault()}
            >
              <span className="flex min-w-0 flex-1 items-center gap-2">
                <span className="truncate">{option.label}</span>
                {option.isStale ? (
                  <Badge variant="secondary" className="text-[10px]">
                    Stale
                  </Badge>
                ) : null}
              </span>
              {option.isStale ? (
                <button
                  type="button"
                  className="ml-auto inline-flex h-5 w-5 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    removeValue(option.value);
                  }}
                  aria-label={`Remove stale ${option.label}`}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              ) : null}
            </DropdownMenuCheckboxItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
