import { useMemo } from "react";
import { FlaskConical } from "lucide-react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SpinnerBlock } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useCacheIsolationProbe } from "@/features/cache-probe/hooks/use-cache-isolation-probe";
import type { CacheProbeCall, CacheProbeVerdict } from "@/features/cache-probe/schemas";
import { useDialogState } from "@/hooks/use-dialog-state";
import { getErrorMessageOrNull } from "@/utils/errors";
import { formatNumber } from "@/utils/formatters";

const VERDICT_VARIANT: Record<CacheProbeVerdict, "destructive" | "secondary" | "outline"> = {
  cross_account_sharing: "destructive",
  no_cross_account_hit: "secondary",
  inconclusive: "outline",
};

function range(from: number, to: number): number[] {
  return Array.from({ length: Math.max(0, to - from + 1) }, (_, index) => from + index);
}

function cachedTokensCell(call: CacheProbeCall): string {
  if (call.cachedTokens == null || call.inputTokens == null) {
    return "—";
  }
  return `${formatNumber(call.cachedTokens)} / ${formatNumber(call.inputTokens)}`;
}

export type CacheIsolationProbeSectionProps = {
  disabled?: boolean;
};

/**
 * Operator diagnostic: does a prefix seeded by one account still read as
 * cached on its siblings?
 *
 * The card is deliberately talkative about how to read the answer. A hit on a
 * non-seed account is proof the cache is shared; the absence of one is not
 * proof of the converse, and a miss on the *seed* account is expected rather
 * than a broken probe. An operator who misreads either will re-run and spend
 * quota for nothing.
 */
export function CacheIsolationProbeSection({ disabled = false }: CacheIsolationProbeSectionProps) {
  const { t } = useTranslation();
  const {
    planQuery,
    runMutation,
    result,
    seedRepetitions,
    otherAccountCount,
    setSeedRepetitions,
    setOtherAccountCount,
  } = useCacheIsolationProbe(!disabled);
  const confirmDialog = useDialogState();

  const plan = planQuery.data;
  const error = useMemo(
    () => getErrorMessageOrNull(planQuery.error) || getErrorMessageOrNull(runMutation.error),
    [planQuery.error, runMutation.error],
  );

  const plannedOtherCount = Math.min(otherAccountCount, plan?.availableOtherAccounts.length ?? 0);
  const plannedCalls = seedRepetitions + plannedOtherCount;
  const estimatedTokens = (plan?.estimatedInputTokensPerCall ?? 0) * plannedCalls;
  const underPressure = plan?.pressure.underPressure ?? false;
  const busy = disabled || runMutation.isPending;

  return (
    <section className="space-y-3 rounded-xl border bg-card p-5" data-testid="cache-isolation-probe">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <FlaskConical className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">{t("cacheProbe.title")}</h3>
          <p className="text-xs text-muted-foreground">{t("cacheProbe.description")}</p>
        </div>
      </div>

      {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

      {planQuery.isLoading ? <SpinnerBlock /> : null}

      {plan ? (
        <>
          {underPressure ? (
            <AlertMessage variant="warning">
              {plan.pressure.detail || t("cacheProbe.pressure.generic")}
            </AlertMessage>
          ) : null}

          <div className="grid gap-2 sm:grid-cols-2">
            <label className="space-y-1">
              <span className="text-xs text-muted-foreground">{t("cacheProbe.controls.seedRepetitions")}</span>
              <Select
                value={String(seedRepetitions)}
                onValueChange={(value) => setSeedRepetitions(Number(value))}
              >
                <SelectTrigger aria-label={t("cacheProbe.controls.seedRepetitions")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {range(1, plan.maxSeedRepetitions).map((value) => (
                    <SelectItem key={value} value={String(value)}>
                      {value}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            <label className="space-y-1">
              <span className="text-xs text-muted-foreground">{t("cacheProbe.controls.otherAccounts")}</span>
              <Select
                value={String(otherAccountCount)}
                onValueChange={(value) => setOtherAccountCount(Number(value))}
              >
                <SelectTrigger aria-label={t("cacheProbe.controls.otherAccounts")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {range(1, plan.maxOtherAccounts).map((value) => (
                    <SelectItem key={value} value={String(value)}>
                      {value}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
          </div>

          <div
            className="flex flex-col gap-3 rounded-lg border px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
            data-testid="cache-isolation-probe-cost"
          >
            <div className="space-y-0.5">
              <p className="text-xs text-muted-foreground">
                {t("cacheProbe.cost.summary", {
                  calls: plannedCalls,
                  tokens: formatNumber(estimatedTokens),
                })}
              </p>
              <p className="text-xs text-muted-foreground">
                {plan.seedAccount
                  ? t("cacheProbe.cost.accounts", {
                      seed: plan.seedAccount.label,
                      others: plannedOtherCount,
                      model: plan.model ?? "—",
                    })
                  : t("cacheProbe.cost.noAccounts")}
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-8 text-xs"
              disabled={busy || underPressure || !plan.seedAccount || plannedOtherCount === 0}
              onClick={() => confirmDialog.show()}
            >
              {runMutation.isPending ? t("cacheProbe.actions.running") : t("cacheProbe.actions.run")}
            </Button>
          </div>

          <AlertMessage variant="warning">{t("cacheProbe.interpretation.seedMisses")}</AlertMessage>
        </>
      ) : null}

      {result ? (
        <div className="space-y-3" data-testid="cache-isolation-probe-result">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={VERDICT_VARIANT[result.verdict]}>
              {t(`cacheProbe.verdicts.${result.verdict}.label`)}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {t("cacheProbe.result.counts", {
                otherHits: result.otherHitCount,
                otherCalls: result.otherCallCount,
                seedHits: result.seedHitCount,
                seedCalls: result.seedCallCount,
              })}
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            {t(`cacheProbe.verdicts.${result.verdict}.detail`)}
          </p>

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("cacheProbe.table.call")}</TableHead>
                <TableHead>{t("cacheProbe.table.account")}</TableHead>
                <TableHead>{t("cacheProbe.table.role")}</TableHead>
                <TableHead>{t("cacheProbe.table.cached")}</TableHead>
                <TableHead>{t("cacheProbe.table.result")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.calls.map((call) => (
                <TableRow key={call.sequence}>
                  <TableCell className="tabular-nums">{call.sequence}</TableCell>
                  <TableCell>{call.accountLabel}</TableCell>
                  <TableCell>{t(`cacheProbe.roles.${call.role}`)}</TableCell>
                  <TableCell className="tabular-nums">{cachedTokensCell(call)}</TableCell>
                  <TableCell>
                    {call.status === "error"
                      ? t("cacheProbe.callStatus.error", { code: call.errorCode ?? "unknown" })
                      : t(`cacheProbe.callStatus.${call.status}`)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}

      <ConfirmDialog
        open={confirmDialog.open}
        title={t("cacheProbe.confirm.title")}
        description={t("cacheProbe.confirm.description", {
          calls: plannedCalls,
          tokens: formatNumber(estimatedTokens),
        })}
        confirmLabel={t("cacheProbe.confirm.confirmLabel")}
        onConfirm={() => {
          confirmDialog.hide();
          runMutation.mutate();
        }}
        onOpenChange={confirmDialog.onOpenChange}
      />
    </section>
  );
}
