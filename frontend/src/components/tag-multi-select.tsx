import { useCallback, useMemo, useState } from "react";
import { ChevronsUpDown, Plus, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";

export type TagMultiSelectProps = {
  value: string[];
  onChange: (value: string[]) => void;
  options: string[];
  placeholder: string;
  loading?: boolean;
  disabled?: boolean;
  allowCustomValues?: boolean;
  clearLabel?: string | null;
  searchPlaceholder?: string;
  emptyLabel?: string;
};

function normalizeTagValue(value: string): string {
  return value.trim().toLowerCase();
}

export function TagMultiSelect({
  value,
  onChange,
  options,
  placeholder,
  loading = false,
  disabled = false,
  allowCustomValues = false,
  clearLabel = null,
  searchPlaceholder = "Search tags...",
  emptyLabel = "No tags found",
}: TagMultiSelectProps) {
  const [search, setSearch] = useState("");

  const normalizedOptions = useMemo(() => {
    const uniqueOptions = new Map<string, string>();
    for (const option of options) {
      const normalized = normalizeTagValue(option);
      if (!normalized || uniqueOptions.has(normalized)) {
        continue;
      }
      uniqueOptions.set(normalized, option.trim());
    }
    return [...uniqueOptions.values()].sort((left, right) => left.localeCompare(right));
  }, [options]);

  const filteredOptions = useMemo(() => {
    if (!search.trim()) {
      return normalizedOptions;
    }
    const needle = search.trim().toLowerCase();
    return normalizedOptions.filter((option) => option.toLowerCase().includes(needle));
  }, [normalizedOptions, search]);

  const selectedComparable = useMemo(
    () => new Set(value.map((tag) => normalizeTagValue(tag))),
    [value],
  );

  const toggle = useCallback(
    (tag: string) => {
      const normalizedTag = normalizeTagValue(tag);
      if (!normalizedTag) {
        return;
      }
      if (selectedComparable.has(normalizedTag)) {
        onChange(value.filter((current) => normalizeTagValue(current) !== normalizedTag));
        return;
      }
      onChange([...value, tag.trim()]);
    },
    [onChange, selectedComparable, value],
  );

  const remove = useCallback(
    (tag: string) => {
      const normalizedTag = normalizeTagValue(tag);
      onChange(value.filter((current) => normalizeTagValue(current) !== normalizedTag));
    },
    [onChange, value],
  );

  const clearSelection = useCallback(() => {
    onChange([]);
  }, [onChange]);

  const createCandidate = search.trim();
  const normalizedCandidate = normalizeTagValue(createCandidate);
  const canCreateCandidate =
    allowCustomValues &&
    normalizedCandidate.length > 0 &&
    !normalizedOptions.some((option) => normalizeTagValue(option) === normalizedCandidate) &&
    !selectedComparable.has(normalizedCandidate);

  const label =
    value.length === 0 ? placeholder : `${value.length} tag${value.length === 1 ? "" : "s"} selected`;

  return (
    <div className="space-y-1.5">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
            className="w-full justify-between font-normal"
            disabled={disabled || loading}
          >
            <span className="truncate text-left">{loading ? "Loading tags..." : label}</span>
            <ChevronsUpDown className="ml-1 size-4 shrink-0 opacity-50" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-[var(--radix-dropdown-menu-trigger-width)] max-h-64">
          <div className="px-2 py-1.5">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={searchPlaceholder}
              className="h-7 text-xs"
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
            />
          </div>
          <DropdownMenuSeparator />
          {clearLabel ? (
            <>
              <DropdownMenuCheckboxItem
                checked={value.length === 0}
                onCheckedChange={clearSelection}
                onSelect={(event) => event.preventDefault()}
              >
                {clearLabel}
              </DropdownMenuCheckboxItem>
              <DropdownMenuSeparator />
            </>
          ) : null}
          {filteredOptions.map((option) => (
            <DropdownMenuCheckboxItem
              key={option}
              checked={selectedComparable.has(normalizeTagValue(option))}
              onCheckedChange={() => toggle(option)}
              onSelect={(event) => event.preventDefault()}
            >
              {option}
            </DropdownMenuCheckboxItem>
          ))}
          {canCreateCandidate ? (
            <>
              {filteredOptions.length > 0 ? <DropdownMenuSeparator /> : null}
              <button
                type="button"
                className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-accent hover:text-accent-foreground"
                onClick={() => {
                  toggle(createCandidate);
                  setSearch("");
                }}
              >
                <Plus className="size-3.5" />
                Add tag "{createCandidate}"
              </button>
            </>
          ) : null}
          {filteredOptions.length === 0 && !canCreateCandidate ? (
            <div className="px-2 py-1.5 text-xs text-muted-foreground">{emptyLabel}</div>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>

      {value.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {value.map((tag) => (
            <Badge key={tag} variant="secondary" className="gap-1 text-xs">
              {tag}
              <button
                type="button"
                className="ml-0.5 hover:text-foreground"
                disabled={disabled}
                onClick={() => {
                  if (!disabled) {
                    remove(tag);
                  }
                }}
              >
                <X className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
      ) : null}
    </div>
  );
}
