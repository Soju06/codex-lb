import { useCallback, useMemo, useState } from "react";
import { ChevronsUpDown, X } from "lucide-react";

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
import { useModelSources } from "@/features/model-sources/hooks/use-model-sources";
import type { ModelSource } from "@/features/model-sources/schemas";

export type ModelSourceMultiSelectProps = {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
};

function sourceSubtitle(source: ModelSource): string {
  const protocols = [
    source.supportsChatCompletions ? "chat" : null,
    source.supportsResponses ? "responses" : null,
    source.supportsAudioTranscriptions ? "audio" : null,
  ].filter(Boolean);
  const count = `${source.models.length} model${source.models.length === 1 ? "" : "s"}`;
  return `${count} · ${protocols.join(", ") || "disabled"}`;
}

export function ModelSourceMultiSelect({
  value,
  onChange,
  placeholder = "All model sources",
}: ModelSourceMultiSelectProps) {
  const { modelSourcesQuery } = useModelSources();
  const sources = useMemo(() => modelSourcesQuery.data?.sources ?? [], [modelSourcesQuery.data]);
  const [search, setSearch] = useState("");
  const selectedSet = useMemo(() => new Set(value), [value]);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return sources;
    return sources.filter(
      (source) =>
        source.id.toLowerCase().includes(query) ||
        source.name.toLowerCase().includes(query) ||
        source.baseUrl.toLowerCase().includes(query) ||
        source.models.some((model) => model.model.toLowerCase().includes(query)),
    );
  }, [search, sources]);

  const selectedSources = useMemo(
    () =>
      value
        .map((sourceId) => sources.find((source) => source.id === sourceId))
        .filter((source): source is ModelSource => source !== undefined),
    [sources, value],
  );

  const toggle = useCallback(
    (sourceId: string) => {
      if (selectedSet.has(sourceId)) {
        onChange(value.filter((current) => current !== sourceId));
        return;
      }
      onChange([...value, sourceId]);
    },
    [onChange, selectedSet, value],
  );

  const remove = useCallback(
    (sourceId: string) => {
      onChange(value.filter((current) => current !== sourceId));
    },
    [onChange, value],
  );

  const label =
    value.length === 0 ? placeholder : `${value.length} source${value.length === 1 ? "" : "s"} selected`;

  return (
    <div className="space-y-1.5">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
            className="w-full justify-between font-normal"
            disabled={modelSourcesQuery.isLoading}
          >
            <span className="truncate text-left">
              {modelSourcesQuery.isLoading ? "Loading model sources..." : label}
            </span>
            <ChevronsUpDown className="ml-1 size-4 shrink-0 opacity-50" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="max-h-64 w-[var(--radix-dropdown-menu-trigger-width)]">
          <div className="px-2 py-1.5">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search sources..."
              className="h-7 text-xs"
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
            />
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuCheckboxItem
            checked={value.length === 0}
            onCheckedChange={() => onChange([])}
            onSelect={(event) => event.preventDefault()}
          >
            All model sources
          </DropdownMenuCheckboxItem>
          <DropdownMenuSeparator />
          {filtered.map((source) => (
            <DropdownMenuCheckboxItem
              key={source.id}
              checked={selectedSet.has(source.id)}
              onCheckedChange={() => toggle(source.id)}
              onSelect={(event) => event.preventDefault()}
              className="items-start"
            >
              <div className="min-w-0 py-0.5">
                <div className="truncate text-sm font-medium">{source.name}</div>
                <div className="truncate text-[11px] text-muted-foreground">{sourceSubtitle(source)}</div>
              </div>
            </DropdownMenuCheckboxItem>
          ))}
          {filtered.length === 0 ? (
            <div className="px-2 py-1.5 text-xs text-muted-foreground">No model sources found</div>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>

      {selectedSources.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {selectedSources.map((source) => (
            <Badge key={source.id} variant="secondary" className="gap-1 text-xs">
              {source.name}
              <button
                type="button"
                className="ml-0.5 hover:text-foreground"
                onClick={() => remove(source.id)}
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
