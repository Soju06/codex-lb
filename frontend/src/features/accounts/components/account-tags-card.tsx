import { useEffect, useMemo, useState } from "react";

import { TagMultiSelect } from "@/components/tag-multi-select";
import { Button } from "@/components/ui/button";
import { useAccountTags } from "@/features/accounts/hooks/use-accounts";

export type AccountTagsCardProps = {
  accountId: string;
  tags: string[];
  disabled: boolean;
  onSave: (accountId: string, tags: string[]) => Promise<void>;
};

function toComparableKey(tags: string[]): string {
  return [...new Set(tags.map((tag) => tag.trim().toLowerCase()).filter(Boolean))].sort().join("::");
}

export function AccountTagsCard({ accountId, tags, disabled, onSave }: AccountTagsCardProps) {
  const { data: availableTags = [], isLoading } = useAccountTags();
  const [selectedTags, setSelectedTags] = useState<string[]>(tags);

  useEffect(() => {
    setSelectedTags(tags);
  }, [tags]);

  const isDirty = useMemo(
    () => toComparableKey(selectedTags) !== toComparableKey(tags),
    [selectedTags, tags],
  );

  return (
    <div className="rounded-md border bg-background/60 px-3 py-2">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Account tags</p>
          <p className="mt-1 text-xs text-muted-foreground">Used by API key tag pools.</p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 text-xs"
          disabled={disabled || !isDirty}
          onClick={() => void onSave(accountId, selectedTags)}
        >
          Save
        </Button>
      </div>
      <div className="mt-3">
        <TagMultiSelect
          value={selectedTags}
          onChange={setSelectedTags}
          options={availableTags}
          placeholder="No tags"
          loading={isLoading}
          disabled={disabled}
          allowCustomValues
          searchPlaceholder="Search or create tags..."
        />
      </div>
    </div>
  );
}
