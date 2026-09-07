import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAccountUsageCapsMutation } from "@/features/accounts/hooks/use-accounts";
import { AccountUsageCapsSchema, type AccountSummary } from "@/features/accounts/schemas";

export function AccountUsageCaps({ account, disabled }: { account: AccountSummary; disabled: boolean }) {
  const { t } = useTranslation();
  const usageCapsMutation = useAccountUsageCapsMutation();
  const [shortCap, setShortCap] = useState(account.usageCap5HPercent?.toString() ?? "");
  const [weeklyCap, setWeeklyCap] = useState(account.usageCapWeeklyPercent?.toString() ?? "");
  const caps = AccountUsageCapsSchema.safeParse({
    usageCap5HPercent: shortCap === "" ? null : Number(shortCap),
    usageCapWeeklyPercent: weeklyCap === "" ? null : Number(weeklyCap),
  });
  const changed = caps.success && (
    caps.data.usageCap5HPercent !== (account.usageCap5HPercent ?? null) ||
    caps.data.usageCapWeeklyPercent !== (account.usageCapWeeklyPercent ?? null)
  );
  const windows = [
    { label: "5h", value: shortCap, set: setShortCap, available: account.windowMinutesPrimary === 300 },
    { label: t("common.quota.weekly"), value: weeklyCap, set: setWeeklyCap, available: account.windowMinutesSecondary === 10080 },
  ];

  return (
    <form onSubmit={(event) => {
      event.preventDefault();
      if (!disabled && !usageCapsMutation.isPending && caps.success && changed) {
        usageCapsMutation.mutate({ accountId: account.accountId, caps: caps.data });
      }
    }} className="space-y-4 rounded-lg border bg-card p-4">
      <fieldset disabled={disabled || usageCapsMutation.isPending} className="space-y-4">
        <div className="space-y-1">
          <legend className="text-sm font-semibold">
            {t("accounts.usageCaps.title", { defaultValue: "Usage caps" })}
          </legend>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("accounts.usageCaps.description", {
              defaultValue: "Reserve quota by stopping new requests at either limit. Traffic resumes once both windows are below their caps. Usage updates and requests already in flight can overshoot a cap.",
            })}
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {windows.map((window) => (
            <div key={window.label} className="space-y-2 rounded-md border bg-muted/20 p-3">
              <label className="grid gap-2.5 text-xs font-medium">
                <span className="block">{t("accounts.usageCaps.label", { defaultValue: "{{window}} cap (% used)", window: window.label })}</span>
                <Input
                  type="number"
                  min={0}
                  max={100}
                  step="any"
                  placeholder={t("accounts.usageCaps.off", { defaultValue: "Off" })}
                  value={window.value}
                  disabled={!window.available && window.value === ""}
                  onChange={(event) => window.set(event.target.value)}
                />
              </label>
              {window.value !== "" ? (
                <Button type="button" variant="outline" size="sm" className="w-full" onClick={() => window.set("")}>
                  {t("accounts.usageCaps.disable", { defaultValue: "Disable cap" })}
                </Button>
              ) : (
                <p className="h-8 content-center text-center text-xs text-muted-foreground">
                  {t("accounts.usageCaps.disabled", { defaultValue: "No cap" })}
                </p>
              )}
            </div>
          ))}
        </div>
        {!caps.success ? <p role="alert" className="text-xs text-destructive">
          {t("accounts.usageCaps.invalid", { defaultValue: "Caps must be greater than 0 and at most 100." })}
        </p> : null}
        {usageCapsMutation.error ? <p role="alert" className="text-xs text-destructive">{usageCapsMutation.error.message}</p> : null}
        <Button type="submit" size="sm" disabled={!caps.success || !changed}>
          {t("accounts.usageCaps.save", { defaultValue: "Save changes" })}
        </Button>
      </fieldset>
    </form>
  );
}
