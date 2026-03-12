import { X } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export type TagInputProps = {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
};

function normalizeTag(value: string): string {
  return value.trim().toLowerCase();
}

export function TagInput({
  value,
  onChange,
  placeholder = "Type a tag and press Enter",
  disabled = false,
}: TagInputProps) {
  const [draft, setDraft] = useState("");
  const tags = useMemo(() => [...value].sort(), [value]);

  const commitDraft = () => {
    const normalized = normalizeTag(draft);
    if (!normalized || value.includes(normalized)) {
      setDraft("");
      return;
    }
    onChange([...value, normalized]);
    setDraft("");
  };

  const removeTag = (tag: string) => {
    onChange(value.filter((entry) => entry !== tag));
  };

  return (
    <div className="space-y-2">
      <Input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === ",") {
            event.preventDefault();
            commitDraft();
          }
        }}
        onBlur={commitDraft}
        placeholder={placeholder}
        disabled={disabled}
        autoComplete="off"
      />
      <div className="flex flex-wrap gap-2">
        {tags.length === 0 ? (
          <p className="text-xs text-muted-foreground">No tags</p>
        ) : (
          tags.map((tag) => (
            <Badge key={tag} variant="secondary" className="gap-1 rounded-full px-2 py-1 text-xs">
              <span>{tag}</span>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="h-4 w-4 rounded-full"
                onClick={() => removeTag(tag)}
                disabled={disabled}
              >
                <X className="size-3" />
                <span className="sr-only">Remove {tag}</span>
              </Button>
            </Badge>
          ))
        )}
      </div>
    </div>
  );
}
