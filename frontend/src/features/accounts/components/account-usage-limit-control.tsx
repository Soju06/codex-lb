import { Gauge, Trash2 } from "lucide-react";
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
  const [draft, setDraft] = useState(
    configuredPercent === null ? "" : formatPercent(configuredPercent),
  );
  const parsedDraft = Number(draft);
  const validDraft =
    draft.trim() !== "" &&
    Number.isFinite(parsedDraft) &&
    parsedDraft > 0 &&
    parsedDraft <= 100;
  const draftChanged =
    configuredPercent === null || parsedDraft !== configuredPercent;
  const disabled = busy || readOnly;
  const inputId = `usage-limit-percent-${account.accountId}`;

  const save = () => {
    if (!validDraft) {
      return;
    }
    onChange(account.accountId, {
      enabled:
        configuredPercent === null
          ? true
          : (account.usageLimitEnabled ?? false),
      percent: parsedDraft,
    });
  };

  return (
    <section
      className="space-y-3 rounded-md border bg-muted/30 p-3"
      aria-label={t("accounts.usageLimit.aria")}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium">
          <Gauge className="h-4 w-4 shrink-0 text-muted-foreground" />
          {t("accounts.usageLimit.title")}
        </div>
        {configuredPercent !== null ? (
          <div className="flex items-center gap-2">
            <UsageLimitStateBadge state={account.usageLimitState ?? "disabled"} />
            <Switch
              aria-label={t("accounts.usageLimit.enableAria")}
              checked={account.usageLimitEnabled ?? false}
              disabled={disabled}
              onCheckedChange={(enabled) =>
                onChange(account.accountId, {
                  enabled,
                  percent: configuredPercent,
                })
              }
            />
          </div>
        ) : null}
      </div>

      {configuredPercent !== null ? (
        <p className="text-xs font-medium">
          {t("accounts.usageLimit.summary", {
            maximum: formatPercent(configuredPercent),
            reserved: formatPercent(100 - configuredPercent),
          })}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">
          {t("accounts.usageLimit.description")}
        </p>
      )}

      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="min-w-0 flex-1 space-y-1" htmlFor={inputId}>
          <span className="text-xs font-medium">
            {t("accounts.usageLimit.maximumUsedPercent")}
          </span>
          <div className="relative">
            <Input
              id={inputId}
              aria-label={t("accounts.usageLimit.maximumUsedPercent")}
              className="h-8 pr-8 text-sm"
              type="number"
              inputMode="decimal"
              min="0"
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
        {configuredPercent !== null ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs"
            disabled={disabled}
            onClick={() =>
              onChange(account.accountId, {
                enabled: false,
                percent: null,
              })
            }
          >
            <Trash2 className="h-3.5 w-3.5" />
            {t("common.actions.remove")}
          </Button>
        ) : null}
      </div>

      {validDraft ? (
        <p className="text-xs text-muted-foreground">
          {t("accounts.usageLimit.summary", {
            maximum: formatPercent(parsedDraft),
            reserved: formatPercent(100 - parsedDraft),
          })}
        </p>
      ) : null}
      <p className="text-xs text-muted-foreground">
        {t("accounts.usageLimit.delayedWarning")}
      </p>
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
  return String(Number(Math.max(0, value).toFixed(2)));
}
