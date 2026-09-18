import { useState } from "react";
import { complementPercent } from "@/utils/quota";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { AccountUsageLimitUpdateRequestSchema } from "@/features/accounts/schemas";
import type { AccountSummary, AccountUsageLimitState, AccountUsageLimitUpdateRequest } from "@/features/accounts/schemas";

type AccountUsageLimitControlProps = {
  account: AccountSummary;
  busy: boolean;
  readOnly: boolean;
  onChange: (accountId: string, update: AccountUsageLimitUpdateRequest) => void;
};

const fields = ["percent", "percent5H", "percentWeekly"] as const;
type LimitField = (typeof fields)[number];
const labels: Record<LimitField, string> = {
  percent: "accounts.usageLimit.maximumUsedPercent",
  percent5H: "accounts.usageLimit.override5h",
  percentWeekly: "accounts.usageLimit.overrideWeekly",
};

export function AccountUsageLimitControl({ account, busy, readOnly, onChange }: AccountUsageLimitControlProps) {
  const { t } = useTranslation();
  const saved = {
    percent: account.usageLimitPercent ?? null,
    percent5H: account.usageLimit5HPercent ?? null,
    percentWeekly: account.usageLimitWeeklyPercent ?? null,
  };
  const savedKey = JSON.stringify([account.accountId, saved]);
  const initialDraft = {
    percent: saved.percent === null ? "" : String(complementPercent(saved.percent)),
    percent5H: saved.percent5H === null ? "" : String(complementPercent(saved.percent5H)),
    percentWeekly: saved.percentWeekly === null ? "" : String(complementPercent(saved.percentWeekly)),
  };
  const [draft, setDraft] = useState({ key: savedKey, values: initialDraft });
  if (draft.key !== savedKey) {
    setDraft({ key: savedKey, values: initialDraft });
  }
  const configured = fields.some((field) => saved[field] !== null);
  const enabled = account.usageLimitEnabled ?? false;
  const disabled = busy || readOnly;
  const parsed = {
    percent: draft.values.percent.trim() === "" ? null : draft.values.percent === initialDraft.percent ? saved.percent : complementPercent(Number(draft.values.percent)),
    percent5H: draft.values.percent5H.trim() === "" ? null : draft.values.percent5H === initialDraft.percent5H ? saved.percent5H : complementPercent(Number(draft.values.percent5H)),
    percentWeekly: draft.values.percentWeekly.trim() === "" ? null : draft.values.percentWeekly === initialDraft.percentWeekly ? saved.percentWeekly : complementPercent(Number(draft.values.percentWeekly)),
  };
  const hasValue = fields.some((field) => parsed[field] !== null);
  const update = { enabled: hasValue && (!configured || enabled), ...parsed };
  const valid = AccountUsageLimitUpdateRequestSchema.safeParse(update).success;
  const changed = fields.some((field) => parsed[field] !== saved[field]);
  const save = () => {
    if (valid && changed) onChange(account.accountId, update);
  };

  return (
    <section className="space-y-3 rounded-md border bg-muted/30 p-3" aria-label={t("accounts.usageLimit.aria")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{t("accounts.usageLimit.title")}</h3>
        {configured ? (
          <div className="flex items-center gap-2">
            <UsageLimitStateBadge state={account.usageLimitState ?? "disabled"} />
            <Switch
              aria-label={t("accounts.usageLimit.enableAria")}
              checked={enabled}
              disabled={disabled}
              onCheckedChange={(nextEnabled) =>
                onChange(account.accountId, nextEnabled ? { enabled: true, ...saved } : { enabled: false })
              }
            />
          </div>
        ) : null}
      </div>
      <p className="text-xs text-muted-foreground">{t("accounts.usageLimit.combinedDescription")}</p>
      <div className="grid gap-3 sm:grid-cols-3">
        {fields.map((field) => {
          const inputId = `usage-limit-${field}-${account.accountId}`;
          const value = parsed[field];
          const invalid = value !== null && (!Number.isFinite(value) || value <= 0 || value > 100);
          return (
            <label key={field} className="min-w-0 space-y-1" htmlFor={inputId}>
              <span className="text-xs font-medium">{t(labels[field])}</span>
              <Input
                id={inputId}
                name={field}
                autoComplete="off"
                aria-invalid={invalid}
                aria-describedby={invalid ? `${inputId}-error` : undefined}
                className="h-8 text-sm"
                type="number"
                inputMode="decimal"
                min="0"
                max="100"
                step="any"
                placeholder={field === "percent" ? t("accounts.usageLimit.noDefault") : t("accounts.usageLimit.inherit")}
                value={draft.values[field]}
                disabled={disabled}
                onChange={(event) => setDraft({ key: savedKey, values: { ...draft.values, [field]: event.target.value } })}
                onKeyDown={(event) => {
                  if (event.key === "Enter") { event.preventDefault(); save(); }
                }}
              />
              {invalid ? <p id={`${inputId}-error`} className="text-xs text-destructive" role="alert">{t("accounts.usageLimit.rangeError")}</p> : null}
            </label>
          );
        })}
      </div>
      <p className="text-xs text-muted-foreground">{t("accounts.usageLimit.windowHint")}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" className="h-8 text-xs" disabled={disabled || !valid || !changed} onClick={save}>
          {configured ? t("common.actions.save") : t("accounts.usageLimit.setAndEnable")}
        </Button>
        {configured ? (
          <Button type="button" size="sm" variant="ghost" className="h-8 text-xs text-muted-foreground"
            disabled={disabled}
            onClick={() => onChange(account.accountId, { enabled: false, percent: null, percent5H: null, percentWeekly: null })}>
            {t("accounts.usageLimit.clearSavedLimit")}
          </Button>
        ) : null}
      </div>
    </section>
  );
}

function UsageLimitStateBadge({ state }: { state: AccountUsageLimitState }) {
  const { t } = useTranslation();
  if (state === "reached" || state === "data_unavailable") {
    return (
      <Badge variant="destructive">
        {t(state === "reached" ? "accounts.usageLimit.states.reached" : "accounts.usageLimit.states.dataUnavailable")}
      </Badge>
    );
  }
  if (state === "available") {
    return <Badge variant="secondary">{t("common.states.active")}</Badge>;
  }
  return <Badge variant="outline">{t("common.states.off")}</Badge>;
}
