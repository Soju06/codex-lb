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
import { useModels } from "@/features/api-keys/hooks/use-models";

export type ModelMultiSelectProps = {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
};

export function ModelMultiSelect({
  value,
  onChange,
  placeholder = "All models",
}: ModelMultiSelectProps) {
  const { data: models = [], isLoading } = useModels();
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    if (!search.trim()) return models;
    const q = search.toLowerCase();
    return models.filter(
      (m) => m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q),
    );
  }, [models, search]);

  const selectedSet = useMemo(() => new Set(value), [value]);

  const toggle = useCallback(
    (modelId: string) => {
      if (selectedSet.has(modelId)) {
        onChange(value.filter((v) => v !== modelId));
      } else {
        onChange([...value, modelId]);
      }
    },
    [selectedSet, value, onChange],
  );

  const remove = useCallback(
    (modelId: string) => {
      onChange(value.filter((v) => v !== modelId));
    },
    [value, onChange],
  );

  const selectAll = useCallback(() => {
    onChange([]);
  }, [onChange]);

  const label =
    value.length === 0 ? placeholder : `${value.length} model${value.length > 1 ? "s" : ""} selected`;

  return (
    <div className="space-y-1.5">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
            className="w-full justify-between font-normal"
            disabled={isLoading}
          >
            <span className="truncate text-left">{isLoading ? "Loading models..." : label}</span>
            <ChevronsUpDown className="ml-1 size-4 shrink-0 opacity-50" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-[var(--radix-dropdown-menu-trigger-width)] max-h-64">
          <div className="px-2 py-1.5">
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search models..."
              className="h-7 text-xs"
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
            />
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuCheckboxItem
            checked={value.length === 0}
            onCheckedChange={selectAll}
            onSelect={(e) => e.preventDefault()}
          >
            All models
          </DropdownMenuCheckboxItem>
          <DropdownMenuSeparator />
          {filtered.map((model) => (
            <DropdownMenuCheckboxItem
              key={model.id}
              checked={selectedSet.has(model.id)}
              onCheckedChange={() => toggle(model.id)}
              onSelect={(e) => e.preventDefault()}
            >
              {model.id}
            </DropdownMenuCheckboxItem>
          ))}
          {filtered.length === 0 ? (
            <div className="px-2 py-1.5 text-xs text-muted-foreground">No models found</div>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>

      {value.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {value.map((modelId) => (
            <Badge key={modelId} variant="secondary" className="gap-1 text-xs">
              {modelId}
              <button
                type="button"
                className="hover:text-foreground ml-0.5"
                onClick={() => remove(modelId)}
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
