import { useState } from "react";

import { TagMultiSelect } from "@/components/tag-multi-select";
import { useAccountTags } from "@/features/accounts/hooks/use-accounts";

export type AccountTagsCardProps = {
  accountId: string;
  tags: string[];
  disabled: boolean;
  onSave: (accountId: string, tags: string[]) => Promise<void>;
};

type PendingSelection = {
  persistedKey: string;
  tags: string[];
};

function toComparableKey(tags: string[]): string {
  return [...new Set(tags.map((tag) => tag.trim().toLowerCase()).filter(Boolean))].sort().join("::");
}

export function AccountTagsCard({ accountId, tags, disabled, onSave }: AccountTagsCardProps) {
  const { data: availableTags = [], isLoading } = useAccountTags();
  const persistedKey = `${accountId}::${toComparableKey(tags)}`;
  const [pendingSelection, setPendingSelection] = useState<PendingSelection | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const selectedTags = pendingSelection?.persistedKey === persistedKey ? pendingSelection.tags : tags;

  function handleChange(nextTags: string[]) {
    if (toComparableKey(nextTags) === toComparableKey(selectedTags)) {
      return;
    }

    setPendingSelection({ persistedKey, tags: nextTags });
    setIsSaving(true);

    void onSave(accountId, nextTags)
      .catch(() => {
        setPendingSelection(null);
      })
      .finally(() => {
        setIsSaving(false);
      });
  }

  return (
    <div className="rounded-md border bg-background/60 px-3 py-2">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Account tags</p>
        <p className="mt-1 text-xs text-muted-foreground">Used by API key tag pools.</p>
      </div>
      <div className="mt-3">
        <TagMultiSelect
          value={selectedTags}
          onChange={handleChange}
          options={availableTags}
          placeholder="No tags"
          loading={isLoading}
          disabled={disabled || isSaving}
          allowCustomValues
          searchPlaceholder="Search or create tags..."
        />
      </div>
    </div>
  );
}
