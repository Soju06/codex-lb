import {
  Activity,
  Download,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Route,
  ShieldCheck,
  Trash2,
  Zap,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type {
  AccountRoutingPolicy,
  AccountSummary,
} from "@/features/accounts/schemas";
import { formatSingleUnitRemaining } from "@/utils/formatters";

export type AccountActionsProps = {
  account: AccountSummary;
  busy: boolean;
  readOnly?: boolean;
  onPause: (accountId: string) => void;
  onResume: (accountId: string) => void;
  onProbe: (accountId: string) => void;
  onDelete: (accountId: string) => void;
  onReauth: () => void;
  onExportAuth: (accountId: string) => void;
  onResetCredit: (accountId: string) => void;
  showResetCreditExpiryBadge?: boolean;
  onSecurityWorkAuthorizedChange: (accountId: string, enabled: boolean) => void;
  onLimitWarmupChange: (accountId: string, enabled: boolean) => void;
  onRoutingPolicyChange: (
    accountId: string,
    routingPolicy: AccountRoutingPolicy,
  ) => void;
};

export function AccountActions({
  account,
  busy,
  readOnly = false,
  onPause,
  onResume,
  onProbe,
  onDelete,
  onReauth,
  onExportAuth,
  onResetCredit,
  showResetCreditExpiryBadge = true,
  onSecurityWorkAuthorizedChange,
  onLimitWarmupChange,
  onRoutingPolicyChange,
}: AccountActionsProps) {
  const { t } = useTranslation();
  const showOperatorRecoveryAction =
    account.status === "reauth_required" || account.status === "deactivated";
  const probeDisabled =
    busy || readOnly || account.status === "paused" || showOperatorRecoveryAction;
  const resetCountdown = showResetCreditExpiryBadge && account.resetCreditNearestExpiresAt
    ? formatSingleUnitRemaining(account.resetCreditNearestExpiresAt)
    : null;
  const availableResetCredits = account.availableResetCredits ?? 0;
  const hasResetCredits = availableResetCredits > 0;
  const resetCreditDisabled =
    busy ||
    readOnly ||
    account.status === "paused" ||
    showOperatorRecoveryAction;

  return (
    <div className="space-y-3 border-t pt-4">
      {!showOperatorRecoveryAction ? (
        <div className="flex flex-col gap-2 rounded-md border bg-muted/30 p-3 sm:flex-row sm:items-center sm:gap-3">
          <div className="flex min-w-0 items-center gap-2 text-sm font-medium sm:min-w-36">
            <Route className="h-4 w-4 text-muted-foreground" />
            {t("accounts.actions.routingPolicy")}
          </div>
          <Select
            value={account.routingPolicy ?? "normal"}
            onValueChange={(value) =>
              onRoutingPolicyChange(
                account.accountId,
                value as AccountRoutingPolicy,
              )
            }
            disabled={busy || readOnly}
          >
            <SelectTrigger
              aria-label={t("accounts.actions.routingPolicy")}
              size="sm"
              className="h-8 w-full min-w-0 text-xs sm:min-w-32 sm:flex-1"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="burn_first">{t("common.routingPolicies.burnFirst")}</SelectItem>
              <SelectItem value="normal">{t("common.routingPolicies.normal")}</SelectItem>
              <SelectItem value="preserve">{t("common.routingPolicies.preserve")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
      ) : null}

      <label
        htmlFor={`security-work-authorized-${account.accountId}`}
        className="flex min-w-0 items-center justify-between gap-3 rounded-md border px-3 py-2"
      >
        <span className="flex min-w-0 items-center gap-2 text-xs font-medium">
          <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">{t("accounts.actions.trustedAccess")}</span>
        </span>
        <Switch
          id={`security-work-authorized-${account.accountId}`}
          className="shrink-0"
          checked={account.securityWorkAuthorized ?? false}
          disabled={busy || readOnly}
          onCheckedChange={(checked) =>
            onSecurityWorkAuthorizedChange(account.accountId, checked)
          }
        />
      </label>

      <div className="flex flex-wrap gap-2">
        {account.status === "paused" ? (
          <Button
            type="button"
            size="sm"
            className="h-8 gap-1.5 text-xs"
            onClick={() => onResume(account.accountId)}
            disabled={busy || readOnly}
          >
            <Play className="h-3.5 w-3.5" />
            {t("common.actions.resume")}
          </Button>
        ) : showOperatorRecoveryAction ? null : (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs"
            onClick={() => onPause(account.accountId)}
            disabled={busy || readOnly}
          >
            <Pause className="h-3.5 w-3.5" />
            {t("common.actions.pause")}
          </Button>
        )}

        {showOperatorRecoveryAction ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs"
            onClick={onReauth}
            disabled={busy || readOnly}
          >
            <RefreshCw className="h-3.5 w-3.5" />
            {t("common.actions.reauthenticate")}
          </Button>
        ) : null}

        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 gap-1.5 text-xs"
          onClick={() => onProbe(account.accountId)}
          disabled={probeDisabled}
        >
          <Activity className="h-3.5 w-3.5" />
          {t("accounts.actions.forceProbe")}
        </Button>

        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 gap-1.5 text-xs"
          onClick={() =>
            onLimitWarmupChange(account.accountId, !account.limitWarmupEnabled)
          }
          disabled={busy || readOnly}
        >
          <Zap className="h-3.5 w-3.5" />
          {account.limitWarmupEnabled ? t("accounts.actions.disableWarmup") : t("accounts.actions.enableWarmup")}
        </Button>

        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 gap-1.5 text-xs"
          onClick={() => onExportAuth(account.accountId)}
          disabled={busy || readOnly}
        >
          <Download className="h-3.5 w-3.5" />
          {t("common.actions.export")}
        </Button>

        {hasResetCredits ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="relative h-8 gap-1.5 pr-8 text-xs"
            onClick={() => onResetCredit(account.accountId)}
            disabled={resetCreditDisabled}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            {t("accounts.actions.resetWithCount", { count: availableResetCredits })}
            {resetCountdown ? (
              <span
                aria-hidden="true"
                className={[
                  "pointer-events-none absolute -top-1 right-1 text-[10px] tabular-nums",
                  resetCountdown.expiringSoon
                    ? "text-destructive"
                    : "text-muted-foreground",
                ].join(" ")}
              >
                {resetCountdown.label}
              </span>
            ) : null}
          </Button>
        ) : null}

        <Button
          type="button"
          size="sm"
          variant="destructive"
          className="h-8 gap-1.5 text-xs"
          onClick={() => onDelete(account.accountId)}
          disabled={busy || readOnly}
        >
          <Trash2 className="h-3.5 w-3.5" />
          {t("common.actions.delete")}
        </Button>
      </div>
    </div>
  );
}
