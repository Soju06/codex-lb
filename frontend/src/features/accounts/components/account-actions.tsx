import { Pause, Play, RefreshCw, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AccountRoutingPolicy, AccountSummary } from "@/features/accounts/schemas";

export type AccountActionsProps = {
  account: AccountSummary;
  busy: boolean;
  onPause: (accountId: string) => void;
  onResume: (accountId: string) => void;
  onDelete: (accountId: string) => void;
  onReauth: () => void;
  onRoutingPolicyChange: (accountId: string, routingPolicy: AccountRoutingPolicy) => void;
};

export function AccountActions({
  account,
  busy,
  onPause,
  onResume,
  onDelete,
  onReauth,
  onRoutingPolicyChange,
}: AccountActionsProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-t pt-4">
      <Select
        value={account.routingPolicy ?? "normal"}
        onValueChange={(value) => onRoutingPolicyChange(account.accountId, value as AccountRoutingPolicy)}
        disabled={busy}
      >
        <SelectTrigger size="sm" className="h-8 w-36 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="burn_first">Burn first</SelectItem>
          <SelectItem value="normal">Normal</SelectItem>
          <SelectItem value="preserve">Preserve</SelectItem>
        </SelectContent>
      </Select>

      {account.status === "paused" ? (
        <Button
          type="button"
          size="sm"
          className="h-8 gap-1.5 text-xs"
          onClick={() => onResume(account.accountId)}
          disabled={busy}
        >
          <Play className="h-3.5 w-3.5" />
          Resume
        </Button>
      ) : (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 gap-1.5 text-xs"
          onClick={() => onPause(account.accountId)}
          disabled={busy}
        >
          <Pause className="h-3.5 w-3.5" />
          Pause
        </Button>
      )}

      {account.status === "deactivated" ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 gap-1.5 text-xs"
          onClick={onReauth}
          disabled={busy}
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Re-authenticate
        </Button>
      ) : null}

      <Button
        type="button"
        size="sm"
        variant="destructive"
        className="h-8 gap-1.5 text-xs"
        onClick={() => onDelete(account.accountId)}
        disabled={busy}
      >
        <Trash2 className="h-3.5 w-3.5" />
        Delete
      </Button>
    </div>
  );
}
