import { AlertTriangle } from "lucide-react";

import { ConfirmDialog } from "@/components/confirm-dialog";
import {
  useAccountMutations,
  useRateLimitResetCredits,
} from "@/features/accounts/hooks/use-accounts";
import type { RateLimitResetCreditItem } from "@/features/accounts/schemas";
import { cn } from "@/lib/utils";
import { formatLocalDateTimeSeconds, formatSingleUnitRemaining } from "@/utils/formatters";

export type ResetCreditConfirmDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  accountId: string | null;
};

function pickSoonestAvailableCredit(
  credits: RateLimitResetCreditItem[] | undefined,
): RateLimitResetCreditItem | null {
  if (!credits || credits.length === 0) {
    return null;
  }
  const available = credits.filter((credit) => credit.status === "available");
  if (available.length === 0) {
    return null;
  }
  return available.reduce((soonest, credit) => {
    const creditExpiresAt = credit.expiresAt
      ? new Date(credit.expiresAt).getTime()
      : Number.POSITIVE_INFINITY;
    const soonestExpiresAt = soonest.expiresAt
      ? new Date(soonest.expiresAt).getTime()
      : Number.POSITIVE_INFINITY;
    return creditExpiresAt < soonestExpiresAt ? credit : soonest;
  });
}

export function ResetCreditConfirmDialog({
  open,
  onOpenChange,
  accountId,
}: ResetCreditConfirmDialogProps) {
  const { resetCreditConsumeMutation } = useAccountMutations();
  const snapshotQuery = useRateLimitResetCredits(accountId, open);
  const soonest = pickSoonestAvailableCredit(snapshotQuery.data?.credits);
  const title = soonest?.title?.trim() || "Rate-limit reset credit";
  const expiresAt = soonest?.expiresAt ?? null;
  const countdown = expiresAt ? formatSingleUnitRemaining(expiresAt) : null;
  const pending = resetCreditConsumeMutation.isPending;

  const handleConfirm = () => {
    if (!accountId || pending) {
      return;
    }
    void resetCreditConsumeMutation
      .mutateAsync(accountId)
      .then(() => {
        onOpenChange(false);
      })
      .catch(() => {
        // onError already surfaced a toast; leave the dialog open for retry.
      });
  };

  const handleOpenChange = (next: boolean) => {
    // Keep the dialog mounted while the redeem request is in-flight so the
    // confirm button can render its gated state and the user can't dismiss
    // mid-request. It closes once the promise settles.
    if (!next && pending) {
      return;
    }
    onOpenChange(next);
  };

  return (
    <ConfirmDialog
      open={open}
      title="Redeem rate-limit reset credit"
      description="This redeems the soonest-expiring banked reset credit for this account."
      confirmLabel={pending ? "Redeeming..." : "Redeem credit"}
      cancelLabel="Cancel"
      confirmDisabled={pending || !accountId || !soonest}
      onOpenChange={handleOpenChange}
      onConfirm={handleConfirm}
    >
      <div className="space-y-3 text-sm">
        <div className="rounded-md border bg-muted/30 px-3 py-2">
          <p className="font-medium">{title}</p>
          {expiresAt ? (
            <p className="mt-1 text-xs text-muted-foreground">
              Expires {formatLocalDateTimeSeconds(expiresAt)}
              {countdown ? (
                <span
                  className={cn(
                    "ml-1 tabular-nums",
                    countdown.expiringSoon ? "text-destructive" : "text-foreground",
                  )}
                >
                  ({countdown.label})
                </span>
              ) : null}
            </p>
          ) : (
            <p className="mt-1 text-xs text-muted-foreground">No expiry provided.</p>
          )}
        </div>
        <p className="flex items-start gap-2 text-xs text-muted-foreground">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden="true" />
          <span>
            This credit is consumed even if the rate-limit window doesn&apos;t move.
          </span>
        </p>
      </div>
    </ConfirmDialog>
  );
}
