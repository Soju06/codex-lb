import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import type {
  AccountSummary,
  AccountUsageLimitState,
  AccountUsageLimitUpdateRequest,
} from "@/features/accounts/schemas";

type AccountUsageLimitControlProps = {
  account: AccountSummary;
  busy: boolean;
  readOnly: boolean;
  onChange: (
    accountId: string,
    update: AccountUsageLimitUpdateRequest,
  ) => void;
};

export function AccountUsageLimitControl({
  account,
  busy,
  readOnly,
  onChange,
}: AccountUsageLimitControlProps) {
  const { t } = useTranslation();
  const configuredPercent = account.usageLimitPercent ?? null;
  const usageLimitEnabled = account.usageLimitEnabled ?? false;
  const [draftState, setDraftState] = useState(() => ({
    accountId: account.accountId,
    configuredPercent,
    value: configuredPercent === null ? "" : formatPercent(configuredPercent),
  }));
  if (
    draftState.accountId !== account.accountId ||
    draftState.configuredPercent !== configuredPercent
  ) {
    setDraftState({
      accountId: account.accountId,
      configuredPercent,
      value: configuredPercent === null ? "" : formatPercent(configuredPercent),
    });
  }
  const draft = draftState.value;
  const setDraft = (value: string) => {
    setDraftState({ accountId: account.accountId, configuredPercent, value });
  };

  const parsedDraft = Number(draft);
  const validDraft =
    draft.trim() !== "" &&
    Number.isFinite(parsedDraft) &&
    parsedDraft > 0 &&
    parsedDraft <= 100;
  const invalidDraft = draft.trim() !== "" && !validDraft;
  const draftChanged =
    configuredPercent === null || parsedDraft !== configuredPercent;
  const disabled = busy || readOnly;
  const inputId = `usage-limit-percent-${account.accountId}`;
  const errorId = `${inputId}-error`;

  const save = () => {
    if (!validDraft || !draftChanged) {
      return;
    }
    onChange(account.accountId, {
      enabled:
        configuredPercent === null
          ? true
          : usageLimitEnabled,
      percent: parsedDraft,
    });
  };

  return (
    <section
      className="space-y-3 rounded-md border bg-muted/30 p-3"
      aria-label={t("accounts.usageLimit.aria")}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{t("accounts.usageLimit.title")}</h3>
        {configuredPercent !== null ? (
          <div className="flex items-center gap-2">
            <UsageLimitStateBadge state={account.usageLimitState ?? "disabled"} />
            <Switch
              aria-label={t("accounts.usageLimit.enableAria")}
              checked={usageLimitEnabled}
              disabled={disabled}
              onCheckedChange={(enabled) =>
                onChange(
                  account.accountId,
                  enabled
                    ? { enabled: true, percent: configuredPercent }
                    : { enabled: false },
                )
              }
            />
          </div>
        ) : null}
      </div>

      <p className={configuredPercent === null ? "text-xs text-muted-foreground" : "text-xs font-medium"}>
        {configuredPercent === null
          ? t("accounts.usageLimit.description")
          : t("accounts.usageLimit.summary", {
              maximum: formatPercent(configuredPercent),
              reserved: formatReservedPercent(configuredPercent),
            })}
      </p>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="min-w-0 flex-1 space-y-1" htmlFor={inputId}>
          <span id={`${inputId}-label`} className="text-xs font-medium">
            {t("accounts.usageLimit.maximumUsedPercent")}
          </span>
          <div className="relative">
            <Input
              id={inputId}
              name="usageLimitPercent"
              autoComplete="off"
              aria-labelledby={`${inputId}-label`}
              aria-invalid={invalidDraft}
              aria-describedby={invalidDraft ? errorId : undefined}
              className="h-8 pr-8 text-sm"
              type="number"
              inputMode="decimal"
              max="100"
              step="any"
              placeholder="10"
              value={draft}
              disabled={disabled}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  save();
                }
              }}
            />
            <span className="pointer-events-none absolute inset-y-0 right-2 flex items-center text-xs text-muted-foreground">
              %
            </span>
          </div>
          {invalidDraft ? (
            <p id={errorId} className="text-xs text-destructive" role="alert">
              {t("accounts.usageLimit.rangeError")}
            </p>
          ) : null}
        </label>
        <Button
          type="button"
          size="sm"
          className="h-8 text-xs"
          disabled={disabled || !validDraft || !draftChanged}
          onClick={save}
        >
          {configuredPercent === null
            ? t("accounts.usageLimit.setAndEnable")
            : t("common.actions.save")}
        </Button>
      </div>

      {validDraft && draftChanged ? (
        <p className="text-xs text-muted-foreground">
          {t("accounts.usageLimit.summary", {
            maximum: formatPercent(parsedDraft),
            reserved: formatReservedPercent(parsedDraft),
          })}
        </p>
      ) : null}
      {usageLimitEnabled || (configuredPercent === null && draft.trim() !== "") ? (
        <p className="text-xs text-muted-foreground">
          {t("accounts.usageLimit.delayedWarning")}
        </p>
      ) : null}
      {configuredPercent !== null ? (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 w-fit px-2 text-xs text-muted-foreground"
          disabled={disabled}
          onClick={() => onChange(account.accountId, { enabled: false, percent: null })}
        >
          {t("accounts.usageLimit.clearSavedLimit")}
        </Button>
      ) : null}
    </section>
  );
}

function UsageLimitStateBadge({ state }: { state: AccountUsageLimitState }) {
  const { t } = useTranslation();
  if (state === "reached") {
    return (
      <Badge variant="destructive">
        {t("accounts.usageLimit.states.reached")}
      </Badge>
    );
  }
  if (state === "data_unavailable") {
    return (
      <Badge variant="destructive">
        {t("accounts.usageLimit.states.dataUnavailable")}
      </Badge>
    );
  }
  if (state === "available") {
    return <Badge variant="secondary">{t("common.states.active")}</Badge>;
  }
  return <Badge variant="outline">{t("common.states.off")}</Badge>;
}

function formatPercent(value: number): string {
  return String(value);
}

function formatReservedPercent(maximumUsedPercent: number): string {
  const decimalPlaces = decimalPlacesIn(maximumUsedPercent);
  const roundedComplement = (100 - maximumUsedPercent).toFixed(
    Math.min(decimalPlaces, 20),
  );
  return String(Number(roundedComplement));
}

function decimalPlacesIn(value: number): number {
  const [coefficient, exponentText] = String(value).toLowerCase().split("e");
  const fractionLength = coefficient.split(".")[1]?.length ?? 0;
  const exponent = exponentText === undefined ? 0 : Number(exponentText);
  return Math.max(0, fractionLength - exponent);
}
