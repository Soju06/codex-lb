import { Route } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AccountRoutingPolicy, AccountSummary } from "@/features/accounts/schemas";

export type AccountRoutingPolicyControlProps = {
  account: AccountSummary;
  busy: boolean;
  onRoutingPolicyChange: (accountId: string, routingPolicy: AccountRoutingPolicy) => void;
};

export function AccountRoutingPolicyControl({
  account,
  busy,
  onRoutingPolicyChange,
}: AccountRoutingPolicyControlProps) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-md border bg-muted/30 p-3">
      <div className="flex min-w-36 items-center gap-2 text-sm font-medium">
        <Route className="h-4 w-4 text-muted-foreground" />
        Routing policy
      </div>
      <Select
        value={account.routingPolicy ?? "normal"}
        onValueChange={(value) => onRoutingPolicyChange(account.accountId, value as AccountRoutingPolicy)}
        disabled={busy}
      >
        <SelectTrigger aria-label="Routing policy" size="sm" className="h-8 w-44 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="burn_first">Burn first</SelectItem>
          <SelectItem value="normal">Normal</SelectItem>
          <SelectItem value="preserve">Preserve</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}
