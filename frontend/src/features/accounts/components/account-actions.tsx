import { Pause, Play, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import type { AccountSummary } from "@/features/accounts/schemas";

export type AccountActionsProps = {
  account: AccountSummary;
  busy: boolean;
  onPause: (accountId: string) => void;
  onResume: (accountId: string) => void;
  onDelete: (accountId: string) => void;
  onReauth: () => void;
  onSecurityWorkAuthorizedChange: (accountId: string, enabled: boolean) => void;
};

export function AccountActions({
  account,
  busy,
  onPause,
  onResume,
  onDelete,
  onReauth,
  onSecurityWorkAuthorizedChange,
}: AccountActionsProps) {
  return (
    <div className="space-y-3 border-t pt-4">
      <label className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
        <span className="flex min-w-0 items-center gap-2 text-xs font-medium">
          <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">Trusted Access for Cyber</span>
        </span>
        <Switch
          checked={account.securityWorkAuthorized ?? false}
          disabled={busy}
          onCheckedChange={(checked) => onSecurityWorkAuthorizedChange(account.accountId, checked)}
        />
      </label>

      <div className="flex flex-wrap gap-2">
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
    </div>
  );
}
