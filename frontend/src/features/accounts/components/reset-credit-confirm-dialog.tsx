import { ConfirmDialog } from "@/components/confirm-dialog";
import {
  useAccountMutations,
  useRateLimitResetCredits,
} from "@/features/accounts/hooks/use-accounts";
import type { RateLimitResetCreditItem } from "@/features/accounts/schemas";
import { cn } from "@/lib/utils";
import { getErrorMessage } from "@/utils/errors";
import { formatLocalDateTimeSeconds, formatSingleUnitRemaining } from "@/utils/formatters";
import { useEffect, useRef } from "react";

export type ResetCreditConfirmDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  accountId: string | null;
  /** Count from account summary when the per-account cache GET has not populated yet. */
  summaryAvailableCount?: number;
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

function CreditExpiryLine({
  expiresAt,
  label,
  suffix,
  colorClass,
}: {
  expiresAt: string | null | undefined;
  label: string;
  suffix?: string;
  colorClass?: string;
}) {
  if (!expiresAt) {
    return <p className="text-xs text-muted-foreground">{label}{suffix ? ` ${suffix}` : ""}</p>;
  }
  const countdown = formatSingleUnitRemaining(expiresAt);
  return (
    <p className="text-xs text-muted-foreground">
      {label}{" "}
      {formatLocalDateTimeSeconds(expiresAt)}{" "}
      <span
        className={cn(
          "tabular-nums",
          colorClass ?? (countdown.expiringSoon ? "text-destructive" : "text-foreground"),
        )}
      >
        ({countdown.label})
      </span>
      {suffix ? ` ${suffix}` : ""}
    </p>
  );
}

function createRedeemRequestId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `dashboard-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function ResetCreditConfirmDialog({
  open,
  onOpenChange,
  accountId,
  summaryAvailableCount = 0,
}: ResetCreditConfirmDialogProps) {
  const { resetCreditConsumeMutation } = useAccountMutations();
  const redeemRequestIdRef = useRef<string | null>(null);
  const snapshotQuery = useRateLimitResetCredits(accountId, open);
  const snapshotLoading = snapshotQuery.isPending;
  const snapshotError = snapshotQuery.isError;
  const snapshotErrorMessage = getErrorMessage(
    snapshotQuery.error,
    "Failed to load reset credit details",
  );
  const soonest = pickSoonestAvailableCredit(snapshotQuery.data?.credits);
  const otherCredits = (snapshotQuery.data?.credits ?? []).filter(
    (c) => c.status === "available" && c.id !== soonest?.id,
  );
  const availableCount = snapshotQuery.isSuccess
    ? (snapshotQuery.data?.availableCount ?? 0)
    : summaryAvailableCount;
  const pending = resetCreditConsumeMutation.isPending;
  const confirmDisabled =
    pending || !accountId || snapshotLoading || snapshotError || availableCount <= 0;

  useEffect(() => {
    if (!open) {
      redeemRequestIdRef.current = null;
    }
  }, [open]);

  const handleConfirm = () => {
    if (!accountId || pending) {
      return;
    }
    redeemRequestIdRef.current = redeemRequestIdRef.current ?? createRedeemRequestId();
    void resetCreditConsumeMutation
      .mutateAsync({ accountId, redeemRequestId: redeemRequestIdRef.current })
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
    if (!next) {
      redeemRequestIdRef.current = null;
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
      confirmDisabled={confirmDisabled}
      keepOpenOnConfirm
      onOpenChange={handleOpenChange}
      onConfirm={handleConfirm}
    >
      <div className="text-sm">
        {snapshotLoading ? (
          <p className="text-xs text-muted-foreground">Loading reset credit details...</p>
        ) : snapshotError ? (
          <p className="text-xs text-destructive">{snapshotErrorMessage}</p>
        ) : (
          <>
            <p className="font-medium">
              {availableCount} free rate limit reset{availableCount !== 1 ? "s" : ""}
            </p>
            {soonest ? (
              <div className="mt-2 space-y-1">
                <CreditExpiryLine
                  expiresAt={soonest.expiresAt}
                  label="Reset expires on"
                  suffix="will be used"
                />
                {otherCredits.map((credit) => (
                  <CreditExpiryLine
                    key={credit.id}
                    expiresAt={credit.expiresAt}
                    label="Other expires on"
                    colorClass="text-muted-foreground"
                  />
                ))}
                {!soonest.expiresAt && otherCredits.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No upcoming expiry data available.</p>
                ) : null}
              </div>
            ) : availableCount > 0 ? (
              <p className="mt-1 text-xs text-muted-foreground">No upcoming expiry data available.</p>
            ) : snapshotQuery.data === null ? (
              <p className="mt-1 text-xs text-muted-foreground">
                Reset credit details are not available yet.
              </p>
            ) : null}
          </>
        )}
      </div>
    </ConfirmDialog>
  );
}
