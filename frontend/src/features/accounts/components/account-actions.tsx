import { Button } from "@/components/ui/button";
import type { AccountSummary } from "@/features/accounts/schemas";

export type AccountActionsProps = {
  account: AccountSummary;
  busy: boolean;
  onPause: (accountId: string) => void;
  onResume: (accountId: string) => void;
  onDelete: (accountId: string) => void;
  onReauth: () => void;
};

export function AccountActions({
  account,
  busy,
  onPause,
  onResume,
  onDelete,
  onReauth,
}: AccountActionsProps) {
  return (
    <div className="flex flex-wrap gap-2">
      {account.status === "paused" ? (
        <Button type="button" size="sm" onClick={() => onResume(account.accountId)} disabled={busy}>
          Resume
        </Button>
      ) : (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => onPause(account.accountId)}
          disabled={busy}
        >
          Pause
        </Button>
      )}

      <Button type="button" size="sm" variant="outline" onClick={onReauth} disabled={busy}>
        Re-authenticate
      </Button>

      <Button
        type="button"
        size="sm"
        variant="destructive"
        onClick={() => onDelete(account.accountId)}
        disabled={busy}
      >
        Delete
      </Button>
    </div>
  );
}
