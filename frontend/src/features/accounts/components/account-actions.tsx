import { Download, Pause, Play, RefreshCw, Route, Trash2, Zap } from "lucide-react";

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
  onExport: (accountId: string) => void;
  onLimitWarmupChange?: (accountId: string, enabled: boolean) => void;
  onExportOpenCodeAuth: (accountId: string) => void;
  onRoutingPolicyChange: (accountId: string, routingPolicy: AccountRoutingPolicy) => void;
};

export function AccountActions({
  account,
  busy,
  onPause,
  onResume,
  onDelete,
  onReauth,
  onExport,
  onLimitWarmupChange,
  onExportOpenCodeAuth,
  onRoutingPolicyChange,
}: AccountActionsProps) {
  const hasWarmupAction = typeof onLimitWarmupChange === "function";
  return (
    <div className="space-y-3 border-t pt-4">
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

      <div className="flex flex-wrap items-center gap-2">
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

        {hasWarmupAction ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs"
            onClick={() => onLimitWarmupChange?.(account.accountId, !account.limitWarmupEnabled)}
            disabled={busy}
          >
            <Zap className="h-3.5 w-3.5" />
            {account.limitWarmupEnabled ? "Disable warm-up" : "Enable warm-up"}
          </Button>
        ) : null}

        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 gap-1.5 text-xs"
          onClick={() => onExport(account.accountId)}
          disabled={busy}
        >
          <Download className="h-3.5 w-3.5" />
          Export
        </Button>

        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 gap-1.5 text-xs"
          onClick={() => onExportOpenCodeAuth(account.accountId)}
          disabled={busy}
        >
          <Download className="h-3.5 w-3.5" />
          Export OpenCode auth
        </Button>

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
