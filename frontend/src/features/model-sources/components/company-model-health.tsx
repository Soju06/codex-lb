import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { ModelSource } from "@/features/model-sources/schemas";

const key = "modelSources.healthChecks";
const timeLabel = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : "—";
const latencyLabel = (value: number | null | undefined) => value == null ? "—" : `${(value / 1000).toFixed(2)}s`;

export function CompanyModelHealth({ source }: { source: ModelSource }) {
  const { t } = useTranslation();
  const status = source.companyStatus;
  if (!status) return null;
  const summary = status.healthCheckSummary;
  const busy = source.maxConcurrency != null && status.inFlight >= source.maxConcurrency;

  return (
    <div className="mt-3 min-w-0 space-y-2 border-t pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-medium">{t(`${key}.title`)}</h4>
        {summary ? <span className="text-xs text-muted-foreground">{t(`${key}.summary`, {
          checked: summary.freshChecks, total: summary.enabledModels,
          visible: summary.visibleModels, hours: summary.freshnessSeconds / 3600,
        })}</span> : null}
      </div>
      {summary ? <p className="text-xs text-muted-foreground">{t(`${key}.schedule`, {
        hours: summary.intervalSeconds / 3600, seconds: summary.timeoutSeconds,
      })}</p> : null}
      <p className="text-xs text-muted-foreground">{t(`${key}.concurrency`, {
        active: status.inFlight, limit: source.maxConcurrency ?? "∞",
      })}{busy ? <span className="ml-2 text-destructive">{t(`${key}.busy`)}</span> : null}</p>
      <div className="max-w-full overflow-x-auto rounded-md border">
        <table className="w-full text-left text-xs" aria-label={`${source.name} ${t(`${key}.title`)}`}>
          <thead className="bg-muted/50 text-muted-foreground">
            <tr>{["model", "result", "latency", "time", "tokens", "catalog"].map((column) => (
              <th key={column} scope="col" className="px-3 py-2 font-medium">{t(`${key}.${column}`)}</th>
            ))}</tr>
          </thead>
          <tbody className="divide-y">
            {source.models.map((model) => {
              const check = model.healthCheck;
              const state = check?.state ?? "unknown";
              const blocked = !source.isEnabled || !model.isEnabled ? "disabled"
                : status.credentialCache !== "present" ? "credentials"
                  : status.budgetExhausted ? "budget" : null;
              return (
                <tr key={model.id}>
                  <th scope="row" className="max-w-64 break-words px-3 py-2 font-mono font-normal">{model.model}</th>
                  <td className="px-3 py-2">
                    <Badge variant={state === "healthy" ? "default" : state === "unhealthy" || state === "slow" ? "destructive" : "secondary"}>
                      {t(`${key}.${state}`)}
                    </Badge>
                    {check?.errorCode ? <p className="mt-1 max-w-48 break-words font-mono text-muted-foreground">{check.errorCode}</p> : null}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 tabular-nums">{latencyLabel(check?.latencyMs)} / {latencyLabel(check?.firstTokenMs)}</td>
                  <td className="whitespace-nowrap px-3 py-2" title={check?.expiresAt ? t(`${key}.expires`, { time: timeLabel(check.expiresAt) }) : undefined}>
                    {check?.checkedAt ? timeLabel(check.checkedAt) : t(`${key}.waiting`)}
                    {check?.nextDueAt ? <p className="mt-1 text-muted-foreground">{t(`${key}.due`, { time: timeLabel(check.nextDueAt) })}</p> : null}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 tabular-nums">{check?.inputTokens ?? "—"} / {check?.outputTokens ?? "—"}</td>
                  <td className="px-3 py-2">
                    {t(`${key}.${check?.catalogVisible ? "visible" : "hidden"}`)}
                    {blocked ? <p className="mt-1 text-muted-foreground">{t(`${key}.${blocked}`)}</p> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">{t(`${key}.scope`)}</p>
    </div>
  );
}
