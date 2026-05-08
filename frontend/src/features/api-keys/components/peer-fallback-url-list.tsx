import { useCallback, useMemo, useState } from "react";
import { Plus, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { normalizePeerFallbackBaseUrl } from "@/features/api-keys/schemas";

export type PeerFallbackUrlListProps = {
  value: string[];
  onChange: (value: string[]) => void;
};

export function PeerFallbackUrlList({ value, onChange }: PeerFallbackUrlListProps) {
  const [baseUrl, setBaseUrl] = useState("");
  const normalizedBaseUrl = useMemo(() => normalizePeerFallbackBaseUrl(baseUrl), [baseUrl]);
  const selectedSet = useMemo(() => new Set(value), [value]);

  const addBaseUrl = useCallback(() => {
    if (!normalizedBaseUrl) {
      return;
    }
    if (selectedSet.has(normalizedBaseUrl)) {
      setBaseUrl("");
      return;
    }
    onChange([...value, normalizedBaseUrl]);
    setBaseUrl("");
  }, [normalizedBaseUrl, onChange, selectedSet, value]);

  const remove = useCallback(
    (targetBaseUrl: string) => {
      onChange(value.filter((current) => current !== targetBaseUrl));
    },
    [onChange, value],
  );

  return (
    <div className="space-y-1.5">
      <div className="flex gap-1.5">
        <Input
          value={baseUrl}
          aria-label="Peer fallback base URL"
          onChange={(event) => setBaseUrl(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              addBaseUrl();
            }
          }}
          placeholder="http://127.0.0.1:2461"
          className="h-8 font-mono text-xs"
        />
        <Button
          type="button"
          size="sm"
          className="h-8 shrink-0 text-xs"
          onClick={addBaseUrl}
          disabled={!normalizedBaseUrl}
        >
          <Plus aria-hidden="true" />
          Add URL
        </Button>
      </div>

      {value.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {value.map((baseUrl) => (
            <Badge key={baseUrl} variant="secondary" className="gap-1 text-xs">
              <span className="max-w-48 truncate font-mono">{baseUrl}</span>
              <button
                type="button"
                className="ml-0.5 hover:text-foreground"
                onClick={() => remove(baseUrl)}
                aria-label={`Remove ${baseUrl}`}
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
