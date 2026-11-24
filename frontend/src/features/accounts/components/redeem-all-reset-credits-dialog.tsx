import { CheckCircle2, XCircle } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { isEmailLabel } from "@/components/blur-email";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useAccountMutations } from "@/features/accounts/hooks/use-accounts";
import type { RedeemAllResult, RedeemAllTarget } from "@/features/accounts/redeem-all";
import { usePrivacyStore } from "@/hooks/use-privacy";

export type RedeemAllResetCreditsDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  targets: RedeemAllTarget[];
};

export function RedeemAllResetCreditsDialog({
  open,
  onOpenChange,
  targets,
}: RedeemAllResetCreditsDialogProps) {
  const { t } = useTranslation();
  const blurred = usePrivacyStore((state) => state.blurred);
  const { redeemAllResetCreditsMutation } = useAccountMutations();
  const { mutateAsync, reset } = redeemAllResetCreditsMutation;
  const pending = redeemAllResetCreditsMutation.isPending;
  const results = redeemAllResetCreditsMutation.data;
  const resultsByAccountId = new Map<string, RedeemAllResult>(
    (results ?? []).map((result) => [result.accountId, result]),
  );

  useEffect(() => {
    if (!open) {
      reset();
    }
  }, [open, reset]);

  const handleConfirm = () => {
    if (pending) {
      return;
    }
    if (results) {
      onOpenChange(false);
      return;
    }
    void mutateAsync(targets.map((target) => target.accountId))
      .then((outcome) => {
        if (outcome.every((result) => result.ok)) {
          onOpenChange(false);
        }
      })
      .catch(() => {
        // The mutation surfaces its own state; keep the dialog open.
      });
  };

  const handleOpenChange = (next: boolean) => {
    // Do not allow dismissing while redeems are in flight: closing would hide
    // the per-account outcome of a half-finished run.
    if (!next && pending) {
      return;
    }
    onOpenChange(next);
  };

  const confirmLabel = pending
    ? t("accounts.redeemAllDialog.redeeming")
    : results
      ? t("common.actions.close")
      : t("accounts.redeemAllDialog.confirm", { count: targets.length });

  return (
    <ConfirmDialog
      open={open}
      title={t("accounts.redeemAllDialog.title")}
      description={t("accounts.redeemAllDialog.description")}
      confirmLabel={confirmLabel}
      cancelLabel={t("common.cancel")}
      confirmDisabled={pending || targets.length === 0}
      keepOpenOnConfirm
      onOpenChange={handleOpenChange}
      onConfirm={handleConfirm}
    >
      <ul className="max-h-64 space-y-1.5 overflow-y-auto text-sm" aria-label={t("accounts.redeemAllDialog.listAria")}>
        {targets.map((target) => {
          const result = resultsByAccountId.get(target.accountId);
          const labelIsEmail = isEmailLabel(target.label, target.email);
          return (
            <li key={target.accountId} className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate font-medium">
                  {labelIsEmail && blurred ? <span className="privacy-blur">{target.label}</span> : target.label}
                </p>
                {result && !result.ok ? (
                  <p className="text-xs text-destructive">{result.error}</p>
                ) : null}
              </div>
              {result ? (
                result.ok ? (
                  <span className="flex shrink-0 items-center gap-1 text-xs text-emerald-600">
                    <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
                    {t("accounts.redeemAllDialog.redeemed")}
                  </span>
                ) : (
                  <span className="flex shrink-0 items-center gap-1 text-xs text-destructive">
                    <XCircle className="h-3.5 w-3.5" aria-hidden />
                    {t("accounts.redeemAllDialog.failed")}
                  </span>
                )
              ) : (
                <span className="shrink-0 text-xs text-muted-foreground">
                  {t("accounts.redeemAllDialog.creditsAvailable", { count: target.availableResetCredits })}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </ConfirmDialog>
  );
}
