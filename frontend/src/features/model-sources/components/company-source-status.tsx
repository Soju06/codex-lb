import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ModelSource } from "@/features/model-sources/schemas";

export function CompanySourceStatus({ source, disabled, onSave }: {
  source: ModelSource;
  disabled: boolean;
  onSave: (budget: number | null) => Promise<unknown>;
}) {
  const { t } = useTranslation();
  const [budget, setBudget] = useState(String(source.localTokenBudget ?? ""));
  const state = source.companyStatus;
  if (!state) return null;
  const value = budget.trim() === "" ? null : Number(budget);
  const valid = value === null || (Number.isSafeInteger(value) && value > 0 && value <= 9_000_000_000_000_000);
  return <div className="space-y-2 border-t pt-2">
    <p>{t("modelSources.governance.health")}: {t(`modelSources.governance.${state.health}`)}</p>
    {state.cooldownUntil ? <p>{t("modelSources.governance.cooldown")}: {new Date(state.cooldownUntil).toLocaleString()}</p> : null}
    <p>{t("modelSources.governance.stats", { success: state.successes, failure: state.failures, limited: state.rateLimits, server: state.serverErrors, latency: state.averageLatencyMs == null ? "—" : Math.round(state.averageLatencyMs) })}</p>
    <p>{t("modelSources.governance.usage", { used: state.budgetUsed, limit: source.localTokenBudget ?? t("modelSources.governance.unlimited") })}</p>
    {state.budgetExhausted ? <p className="text-destructive">{t("modelSources.governance.exhausted")}</p> : null}
    <form className="flex flex-wrap items-center gap-2" onSubmit={(event) => {
      event.preventDefault();
      if (valid) void onSave(value).catch(() => undefined);
    }}>
      <label htmlFor={`budget-${source.id}`}>{t("modelSources.governance.budget")}</label>
      <Input id={`budget-${source.id}`} className="h-8 w-44" type="number" min="1" step="1" value={budget} onChange={(event) => setBudget(event.target.value)} disabled={disabled} placeholder={t("modelSources.governance.unlimited")} />
      <Button type="submit" size="sm" disabled={disabled || !valid}>{t("modelSources.governance.save")}</Button>
    </form>
    <p>{t("modelSources.governance.soft")}</p>
  </div>;
}
