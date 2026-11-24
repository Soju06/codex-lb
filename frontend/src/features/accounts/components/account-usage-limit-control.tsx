import { useState } from "react";
import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import type { AccountSummary, AccountUsageLimitUpdateRequest } from "@/features/accounts/schemas";
import { cn } from "@/lib/utils";
import { complementPercent } from "@/utils/quota";

type AccountUsageLimitControlProps = {
  account: AccountSummary;
  busy: boolean;
  readOnly: boolean;
  onChange: (accountId: string, update: AccountUsageLimitUpdateRequest) => void;
};

type LimitField = "percent" | "percent5H" | "percentWeekly";
type Caps = Record<LimitField, number | null>;
type Row = { field: LimitField; label: string; remaining: number | null };

// The API requires a positive maximum-used value, so 100% reserve is excluded.
const MAX_RESERVE_PERCENT = 99.99999999999999;
// A maximum-used cap of 100 keeps nothing back.
const NO_RESERVE = 100;
const WINDOW_FIELDS = { 300: "percent5H", 10080: "percentWeekly" } as const;

export function AccountUsageLimitControl({ account, busy, readOnly, onChange }: AccountUsageLimitControlProps) {
  const { t } = useTranslation();
  const saved: Caps = {
    percent: account.usageLimitPercent ?? null,
    percent5H: account.usageLimit5HPercent ?? null,
    percentWeekly: account.usageLimitWeeklyPercent ?? null,
  };
  const rows = reserveRows(account, t);
  // Each row shows the reserve that applies to its window, so a window without an override shows the default.
  const savedCaps = rows.map(({ field }) => (field === "percent" ? saved.percent : saved[field] ?? saved.percent));
  const initialDraft = savedCaps.map((cap) => (cap === null ? "" : String(complementPercent(cap))));
  const savedKey = JSON.stringify([account.accountId, saved, rows.map(({ field }) => field)]);
  const [draft, setDraft] = useState({ key: savedKey, values: initialDraft });
  if (draft.key !== savedKey) {
    setDraft({ key: savedKey, values: initialDraft });
  }

  const configured = Object.values(saved).some((value) => value !== null);
  const enabled = account.usageLimitEnabled ?? false;
  const disabled = busy || readOnly;
  // Saving a new policy turns it on, so the preview already applies the draft.
  const applies = configured ? enabled : true;
  const reserves = draft.values.map((value) => (value.trim() === "" ? 0 : Number(value)));
  // Untouched rows keep their exact saved cap, so only edited text needs validation.
  const invalid = reserves.map((value, index) =>
    draft.values[index] !== initialDraft[index] && (!Number.isFinite(value) || value < 0 || value >= 100));
  const caps = draft.values.map((value, index) =>
    value === initialDraft[index] ? savedCaps[index] ?? NO_RESERVE : complementPercent(reserves[index]),
  );
  const draftChanged = draft.values.some((value, index) => value !== initialDraft[index]);
  const valid = !invalid.some(Boolean);
  const update = buildUpdate(rows, caps, saved, configured ? enabled : true);
  const removes = update.enabled === false && update.percent === null && update.percent5H === null && update.percentWeekly === null;
  // A saved policy with no reserve in any shown row (for example, only an override for a window the
  // account no longer reports) is offered for removal without an edit; without a saved policy it is a no-op.
  const changed = removes ? configured : draftChanged;
  const errorId = `usage-limit-error-${account.accountId}`;
  const save = () => {
    if (valid && changed && !disabled) onChange(account.accountId, update);
  };
  const state = account.usageLimitState;
  const blocked = enabled && (state === "reached" || state === "data_unavailable");

  return (
    <section className="space-y-3 rounded-md border bg-muted/30 p-3" aria-label={t("accounts.usageLimit.aria")}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium">{t("accounts.usageLimit.title")}</h3>
          {blocked ? <StatusBadge status={state === "reached" ? "limitReached" : "usageUnavailable"} /> : null}
        </div>
        <Switch
          aria-label={t("accounts.usageLimit.enableAria")}
          checked={enabled}
          disabled={disabled || !configured}
          onCheckedChange={(nextEnabled) => onChange(account.accountId, { enabled: nextEnabled })}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        {t(configured && !enabled ? "accounts.usageLimit.offDescription" : "accounts.usageLimit.onDescription")}
      </p>
      <table className="w-full text-xs tabular-nums sm:w-auto">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th scope="col" className="pb-1 pr-6 font-medium">{t("accounts.usageLimit.columns.window")}</th>
            <th scope="col" className="pb-1 pr-6 font-medium">{t("accounts.usageLimit.columns.reserve")}</th>
            <th scope="col" className="min-w-20 pb-1 pr-6 text-right font-medium">{t("accounts.usageLimit.columns.remaining")}</th>
            <th scope="col" className="min-w-28 pb-1 text-right font-medium">{t("accounts.usageLimit.columns.usable")}</th>
          </tr>
        </thead>
        <tbody className={cn(!applies && "text-muted-foreground")}>
          {rows.map((row, index) => {
            const inputId = `usage-limit-${row.field}-${account.accountId}`;
            const reserve = invalid[index] ? null : reserves[index];
            const usable = row.remaining === null || reserve === null
              ? null
              : applies ? Math.max(0, row.remaining - reserve) : row.remaining;
            const reached = applies && reserve !== null && reserve > 0 && row.remaining !== null && row.remaining <= reserve;
            return (
              <tr key={row.field}>
                <th scope="row" className="py-1 pr-6 text-left font-normal">
                  <label htmlFor={inputId}>{row.label}</label>
                </th>
                <td className="py-1 pr-6">
                  <div className="relative w-20">
                    <Input
                      id={inputId}
                      name={row.field}
                      autoComplete="off"
                      aria-invalid={invalid[index]}
                      aria-describedby={invalid[index] ? errorId : undefined}
                      className={cn(
                        "h-7 [appearance:textfield] pr-6 text-right text-xs [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none",
                        draft.values[index] !== initialDraft[index] && "border-ring")}
                      type="number"
                      inputMode="decimal"
                      min="0"
                      max={MAX_RESERVE_PERCENT}
                      step="any"
                      placeholder="0"
                      value={draft.values[index]}
                      disabled={disabled}
                      onChange={(event) => {
                        const values = [...draft.values];
                        values[index] = event.target.value;
                        setDraft({ key: savedKey, values });
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") { event.preventDefault(); save(); }
                        if (event.key === "Escape") setDraft({ key: savedKey, values: initialDraft });
                      }}
                    />
                    <span aria-hidden className="pointer-events-none absolute inset-y-0 right-2 flex items-center text-muted-foreground">%</span>
                  </div>
                </td>
                <td className="py-1 pr-6 text-right">{formatPercent(row.remaining)}</td>
                <td className={cn("py-1 text-right", reached && "text-orange-700 dark:text-orange-400")}>
                  {reached ? t("accounts.usageLimit.reachedValue") : formatPercent(usable)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {invalid.some(Boolean) ? (
        <p id={errorId} className="text-xs text-destructive" role="alert">
          {t("accounts.usageLimit.rangeError")}
        </p>
      ) : null}
      {changed ? (
        <div className="flex gap-2">
          <Button type="button" size="sm" className="h-7 text-xs" disabled={disabled || !valid} onClick={save}>
            {t(removes ? "accounts.usageLimit.remove" : configured ? "common.actions.save" : "accounts.usageLimit.setAndEnable")}
          </Button>
          {draftChanged ? (
            <Button type="button" size="sm" variant="outline" className="h-7 text-xs" disabled={disabled}
              onClick={() => setDraft({ key: savedKey, values: initialDraft })}>
              {t("common.cancel")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function reserveRows(account: AccountSummary, t: (key: string) => string): Row[] {
  const usage = account.usage;
  const slots = [
    { minutes: account.windowMinutesPrimary, remaining: usage?.primaryRemainingPercent ?? null },
    { minutes: account.windowMinutesSecondary, remaining: usage?.secondaryRemainingPercent ?? null },
  ];
  const rows: Row[] = [];
  for (const minutes of [300, 10080] as const) {
    const slot = slots.find((item) => item.minutes === minutes);
    if (slot) {
      rows.push({
        field: WINDOW_FIELDS[minutes],
        label: t(minutes === 300 ? "accounts.usageLimit.windows.fiveHour" : "accounts.usageLimit.windows.weekly"),
        remaining: slot.remaining,
      });
    }
  }
  // Monthly, nonstandard, and unidentified windows follow the shared default only.
  const governed = [
    ...slots.filter((slot) => (slot.minutes != null || slot.remaining != null) && slot.minutes !== 300 && slot.minutes !== 10080),
    ...(account.windowMinutesMonthly != null || usage?.monthlyRemainingPercent != null
      ? [{ minutes: account.windowMinutesMonthly, remaining: usage?.monthlyRemainingPercent ?? null, monthly: true }]
      : []),
  ];
  if (rows.length === 0 || governed.length > 0) {
    const label = rows.length > 0
      ? "accounts.usageLimit.windows.other"
      : governed.length === 1 && "monthly" in governed[0] ? "accounts.usageLimit.windows.monthly" : "accounts.usageLimit.windows.all";
    rows.push({ field: "percent", label: t(label), remaining: governed.length === 1 ? governed[0].remaining : null });
  }
  return rows;
}

function buildUpdate(rows: Row[], caps: number[], saved: Caps, enabled: boolean): AccountUsageLimitUpdateRequest {
  if (caps.every((cap) => cap === NO_RESERVE)) {
    return { enabled: false, percent: null, percent5H: null, percentWeekly: null };
  }
  const defaultIndex = rows.findIndex((row) => row.field === "percent");
  const fallback = defaultIndex === -1 ? saved.percent : caps[defaultIndex] === NO_RESERVE ? null : caps[defaultIndex];
  const update: AccountUsageLimitUpdateRequest = { enabled };
  if (defaultIndex !== -1) update.percent = fallback;
  rows.forEach((row, index) => {
    if (row.field === "percent") return;
    // A window matching the default inherits it; "no reserve" needs an explicit override only when a default exists.
    const cap = caps[index];
    update[row.field] = cap === (fallback ?? NO_RESERVE) ? null : cap;
  });
  return update;
}

function formatPercent(value: number | null): string {
  return value === null ? "—" : `${Number(value.toFixed(2))}%`;
}
