import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { ApiKeyQuotaBars } from "./api-key-quota-bars";
import { apiKeyStatus } from "@/features/apis/list-utils";
import type { ApiKey } from "@/features/api-keys/schemas";

export type ApiListItemProps = {
  apiKey: ApiKey;
  selected: boolean;
  onSelect: (keyId: string) => void;
};

export function ApiListItem({ apiKey, selected, onSelect }: ApiListItemProps) {
  const { t } = useTranslation();
  const status = apiKeyStatus(apiKey);
  return (
    <button
      type="button"
      onClick={() => onSelect(apiKey.id)}
      className={cn(
        "w-full rounded-lg px-3 py-2.5 text-left transition-colors",
        selected ? "bg-primary/8 ring-1 ring-primary/25" : "hover:bg-muted/50",
      )}
    >
      <div className="flex items-center gap-2.5">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{apiKey.name}</p>
        </div>
        <Badge
          className={cn(
            status !== "active"
              ? "bg-zinc-500 text-white"
              : "bg-emerald-500 text-white",
          )}
        >
          {t(`common.states.${status}`)}
        </Badge>
      </div>
      <ApiKeyQuotaBars apiKey={apiKey} />
    </button>
  );
}
