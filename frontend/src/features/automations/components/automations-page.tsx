import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bot, Inbox, Info, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { isEmailLabel } from "@/components/blur-email";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { SpinnerBlock } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { useModels } from "@/features/api-keys/hooks/use-models";
import {
  buildAccountDisplayIndex,
  formatAccountsSummary,
  resolveAccountDisplay,
} from "@/features/automations/account-display";
import { AutomationJobDialog } from "@/features/automations/components/automation-job-dialog";
import {
  AutomationJobsFilters,
  AutomationRunsFilters,
} from "@/features/automations/components/automation-list-filters";
import { RunDetailsDialog } from "@/features/automations/components/run-details-dialog";
import {
  formatRunStatusLabel,
  runStatusVariant,
} from "@/features/automations/components/run-status-utils";
import { getAutomationRunDetails } from "@/features/automations/api";
import { useAutomationListing } from "@/features/automations/hooks/use-automation-listing";
import { useAutomations } from "@/features/automations/hooks/use-automations";
import { formatScheduleTimeForInput } from "@/features/automations/time-utils";
import { PaginationControls } from "@/features/dashboard/components/filters/pagination-controls";
import { useDialogState } from "@/hooks/use-dialog-state";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { useTimeFormatStore, type TimeFormatPreference } from "@/hooks/use-time-format";
import { getErrorMessageOrNull } from "@/utils/errors";
import { formatModelLabel, formatSlug, formatTimeLong } from "@/utils/formatters";
import type {
  AutomationJob,
  AutomationRun,
  AutomationRunStatus,
  AutomationScheduleDay,
} from "@/features/automations/schemas";

const EMPTY_AUTOMATION_JOBS: AutomationJob[] = [];
const EMPTY_AUTOMATION_RUNS: AutomationRun[] = [];
const SERVER_DEFAULT_TIMEZONE = "server_default";
const JOB_STATUS_FILTER_VALUES = ["enabled", "disabled"] as const;
const RUN_STATUS_FILTER_VALUES = ["running", "success", "partial", "failed"] as const;
const RUN_TRIGGER_FILTER_VALUES = ["scheduled", "manual"] as const;
const JOB_TYPE_FILTER_VALUES = ["daily"] as const;

function formatScheduleDayLabel(day: AutomationScheduleDay, t: ReturnType<typeof useTranslation>["t"]): string {
  return t(`settings.routing.workingDays.days.${day}`, { defaultValue: day.toUpperCase() });
}

function formatScheduleDays(days: AutomationScheduleDay[], t: ReturnType<typeof useTranslation>["t"]): string {
  if (days.length === 7) {
    return t("automations.schedule.everyDay");
  }
  if (days.join(",") === "mon,tue,wed,thu,fri") {
    return t("automations.schedule.weekdays");
  }
  return days
    .map((day) => formatScheduleDayLabel(day, t))
    .join(", ");
}

function formatHourValue(value: string, timeFormat: TimeFormatPreference): string {
  return formatScheduleTimeForInput(value, timeFormat);
}

function formatScheduleSummary(
  days: AutomationScheduleDay[],
  time: string,
  timeFormat: TimeFormatPreference,
  t: ReturnType<typeof useTranslation>["t"],
): string {
  const hour = formatHourValue(time, timeFormat);
  if (days.length === 7) {
    return t("automations.schedule.everyDayAt", { time: hour });
  }
  const serializedDays = days.join(",");
  if (serializedDays === "mon,tue,wed,thu,fri") {
    return t("automations.schedule.weekdaysAt", { time: hour });
  }
  if (serializedDays === "sat,sun") {
    return t("automations.schedule.weekendsAt", { time: hour });
  }
  return t("automations.schedule.daysAt", { days: formatScheduleDays(days, t), time: hour });
}

function formatTimezoneLabel(value: string, t: ReturnType<typeof useTranslation>["t"]): string {
  return value === SERVER_DEFAULT_TIMEZONE ? t("automations.schedule.serverDefault") : value;
}

function formatTypeLabel(value: string, t: ReturnType<typeof useTranslation>["t"]): string {
  if (value === "daily") {
    return t("automations.types.daily");
  }
  return value;
}

function formatStatusLabel(value: string, t: ReturnType<typeof useTranslation>["t"]): string {
  return t(`automations.statuses.${value}`, { defaultValue: formatSlug(value) });
}


function hasJobsFiltersApplied(filters: {
  search: string;
  accountIds: string[];
  models: string[];
  statuses: string[];
  scheduleTypes: string[];
}): boolean {
  return (
    filters.search.trim().length > 0 ||
    filters.accountIds.length > 0 ||
    filters.models.length > 0 ||
    filters.statuses.length > 0 ||
    filters.scheduleTypes.length > 0
  );
}

function hasRunsFiltersApplied(filters: {
  search: string;
  accountIds: string[];
  models: string[];
  statuses: string[];
  triggers: string[];
}): boolean {
  return (
    filters.search.trim().length > 0 ||
    filters.accountIds.length > 0 ||
    filters.models.length > 0 ||
    filters.statuses.length > 0 ||
    filters.triggers.length > 0
  );
}

export function AutomationsPage() {
  const { t } = useTranslation();
  const [editingJob, setEditingJob] = useState<AutomationJob | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const timeFormat = useTimeFormatStore((state) => state.timeFormat);
  const blurred = usePrivacyStore((state) => state.blurred);
  const createDialog = useDialogState();
  const deleteDialog = useDialogState<AutomationJob>();
  const runNowDialog = useDialogState<AutomationJob>();

  const { data: models = [], isLoading: modelsLoading } = useModels();
  const { accountsQuery } = useAccounts();
  const {
    jobsFilters,
    runsFilters,
    jobsQuery,
    runsQuery,
    jobOptionsQuery,
    runOptionsQuery,
    updateJobsFilters,
    updateRunsFilters,
    resetJobsFilters,
    resetRunsFilters,
  } = useAutomationListing();
  const {
    createMutation,
    updateMutation,
    deleteMutation,
    runNowMutation,
  } = useAutomations(null, { enableQueries: false });
  const runDetailsQuery = useQuery({
    queryKey: ["automations", "run-details", selectedRunId],
    queryFn: () => getAutomationRunDetails(selectedRunId ?? ""),
    enabled: selectedRunId !== null,
    refetchOnWindowFocus: false,
  });

  const jobs = jobsQuery.data?.items ?? EMPTY_AUTOMATION_JOBS;
  const runs = runsQuery.data?.items ?? EMPTY_AUTOMATION_RUNS;
  const accountDisplayIndex = useMemo(
    () => buildAccountDisplayIndex(accountsQuery.data ?? []),
    [accountsQuery.data],
  );
  const accountRecordsById = useMemo(
    () =>
      new Map(
        (accountsQuery.data ?? []).map((account) => [account.accountId, account]),
      ),
    [accountsQuery.data],
  );
  const accountBlurIndex = useMemo(() => {
    const index = new Map<string, { primary: boolean; secondary: boolean; any: boolean }>();
    for (const account of accountsQuery.data ?? []) {
      const displayName = (account.displayName ?? "").trim();
      const email = (account.email ?? "").trim();
      const primary = displayName || email || "Unnamed account";
      const hasDistinctEmail =
        displayName.length > 0 &&
        email.length > 0 &&
        displayName.toLowerCase() !== email.toLowerCase();
      const primaryBlur = isEmailLabel(primary, email);
      const secondaryBlur = hasDistinctEmail && isEmailLabel(email, email);
      index.set(account.accountId, {
        primary: primaryBlur,
        secondary: secondaryBlur,
        any: primaryBlur || secondaryBlur,
      });
    }
    return index;
  }, [accountsQuery.data]);
  const automationModels = useMemo(
    () => models.filter((entry) => !entry.sourceOnly),
    [models],
  );

  const jobsAccountOptions = useMemo(
    () => {
      const accountIds = [...new Set([
        ...(jobOptionsQuery.data?.accountIds ?? []),
        ...(accountsQuery.data ?? []).map((account) => account.accountId),
      ])].sort();
      return accountIds.map((accountId) => {
        const display = resolveAccountDisplay(accountId, accountDisplayIndex);
        const account = accountRecordsById.get(accountId);
        const email = (account?.email ?? "").trim();
        const shouldBlur =
          accountBlurIndex.get(accountId)?.any ?? (email.length > 0 && isEmailLabel(display.primary, email));
        return {
          value: accountId,
          label: display.secondary ? `${display.primary} (${display.secondary})` : display.primary,
          isEmail: shouldBlur,
        };
      });
    },
    [accountBlurIndex, accountDisplayIndex, accountRecordsById, accountsQuery.data, jobOptionsQuery.data?.accountIds],
  );

  const jobsModelOptions = useMemo(
    () => {
      const unique = [...new Set([
        ...automationModels.map((entry) => entry.id.trim()),
        ...(jobOptionsQuery.data?.models ?? []).map((entry) => entry.trim()),
      ])]
        .filter((entry) => entry.length > 0)
        .sort();
      return unique.map((entry) => ({
        value: entry,
        label: entry,
      }));
    },
    [automationModels, jobOptionsQuery.data?.models],
  );

  const jobsStatusOptions = useMemo(
    () =>
      JOB_STATUS_FILTER_VALUES.map((entry) => ({
        value: entry,
        label: formatStatusLabel(entry, t),
      })),
    [t],
  );

  const jobsScheduleTypeOptions = useMemo(
    () => {
      const scheduleTypes = (jobOptionsQuery.data?.scheduleTypes?.length
        ? jobOptionsQuery.data.scheduleTypes
        : [...JOB_TYPE_FILTER_VALUES]) as string[];
      return scheduleTypes.map((entry) => ({
        value: entry,
        label: formatTypeLabel(entry, t),
      }));
    },
    [jobOptionsQuery.data, t],
  );

  const runsAccountOptions = useMemo(
    () => {
      const accountIds = [...new Set([
        ...(runOptionsQuery.data?.accountIds ?? []),
        ...(accountsQuery.data ?? []).map((account) => account.accountId),
      ])].sort();
      return accountIds.map((accountId) => {
        const display = resolveAccountDisplay(accountId, accountDisplayIndex);
        const account = accountRecordsById.get(accountId);
        const email = (account?.email ?? "").trim();
        const shouldBlur =
          accountBlurIndex.get(accountId)?.any ?? (email.length > 0 && isEmailLabel(display.primary, email));
        return {
          value: accountId,
          label: display.secondary ? `${display.primary} (${display.secondary})` : display.primary,
          isEmail: shouldBlur,
        };
      });
    },
    [accountBlurIndex, accountDisplayIndex, accountRecordsById, accountsQuery.data, runOptionsQuery.data?.accountIds],
  );

  const runsModelOptions = useMemo(
    () => {
      const unique = [...new Set([
        ...automationModels.map((entry) => entry.id.trim()),
        ...(runOptionsQuery.data?.models ?? []).map((entry) => entry.trim()),
      ])]
        .filter((entry) => entry.length > 0)
        .sort();
      return unique.map((entry) => ({
        value: entry,
        label: entry,
      }));
    },
    [automationModels, runOptionsQuery.data?.models],
  );

  const runsStatusOptions = useMemo(
    () =>
      RUN_STATUS_FILTER_VALUES.map((entry) => ({
        value: entry,
        label: formatStatusLabel(entry, t),
      })),
    [t],
  );

  const runsTriggerOptions = useMemo(
    () =>
      RUN_TRIGGER_FILTER_VALUES.map((entry) => ({
        value: entry,
        label: formatStatusLabel(entry, t),
      })),
    [t],
  );

  const jobsTotal = jobsQuery.data?.total ?? 0;
  const jobsHasMore = jobsQuery.data?.hasMore ?? false;
  const runsTotal = runsQuery.data?.total ?? 0;
  const runsHasMore = runsQuery.data?.hasMore ?? false;
  const jobsFiltersApplied = hasJobsFiltersApplied(jobsFilters);
  const runsFiltersApplied = hasRunsFiltersApplied(runsFilters);

  const busy =
    createMutation.isPending ||
    updateMutation.isPending ||
    deleteMutation.isPending ||
    runNowMutation.isPending;

  const errorMessage =
    getErrorMessageOrNull(jobsQuery.error) ||
    getErrorMessageOrNull(runsQuery.error) ||
    getErrorMessageOrNull(jobOptionsQuery.error) ||
    getErrorMessageOrNull(runOptionsQuery.error) ||
    getErrorMessageOrNull(runDetailsQuery.error) ||
    getErrorMessageOrNull(createMutation.error) ||
    getErrorMessageOrNull(updateMutation.error) ||
    getErrorMessageOrNull(deleteMutation.error) ||
    getErrorMessageOrNull(runNowMutation.error);

  const openCreateDialog = () => {
    setEditingJob(null);
    createDialog.show();
  };

  const openEditDialog = (job: AutomationJob) => {
    setEditingJob(job);
    createDialog.show();
  };

  const handleCreate = async (payload: {
    name: string;
    enabled: boolean;
    schedule: {
      type: "daily";
      time: string;
      timezone: string;
      thresholdMinutes: number;
      days: ("mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun")[];
    };
    model: string;
    reasoningEffort?: "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | "ultra" | null;
    prompt?: string;
    accountIds: string[];
  }) => {
    await createMutation.mutateAsync(payload);
    setEditingJob(null);
  };

  const handleUpdate = async (automationId: string, payload: {
    name?: string;
    enabled?: boolean;
    schedule?: {
      type: "daily";
      time: string;
      timezone: string;
      thresholdMinutes: number;
      days: ("mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun")[];
    };
    model?: string;
    reasoningEffort?: "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | "ultra" | null;
    prompt?: string;
    accountIds?: string[];
  }) => {
    await updateMutation.mutateAsync({ automationId, payload });
    setEditingJob(null);
  };

  return (
    <div className="animate-fade-in-up space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("automations.page.title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("automations.page.subtitle")}
        </p>
      </div>

      {errorMessage ? <AlertMessage variant="error">{errorMessage}</AlertMessage> : null}

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold">{t("automations.jobs.title")}</h3>
            <p className="text-xs text-muted-foreground">{t("automations.jobs.description")}</p>
          </div>
          <Button type="button" size="sm" onClick={openCreateDialog}>
            <Plus className="mr-1.5 h-4 w-4" />
            {t("automations.jobs.add")}
          </Button>
        </div>

        <AutomationJobsFilters
          filters={jobsFilters}
          accountOptions={jobsAccountOptions}
          modelOptions={jobsModelOptions}
          statusOptions={jobsStatusOptions}
          scheduleTypeOptions={jobsScheduleTypeOptions}
          onSearchChange={(search) => updateJobsFilters({ search, offset: 0 })}
          onAccountChange={(accountIdsFilter) => updateJobsFilters({ accountIds: accountIdsFilter, offset: 0 })}
          onModelChange={(modelsFilter) => updateJobsFilters({ models: modelsFilter, offset: 0 })}
          onStatusChange={(statuses) => updateJobsFilters({ statuses, offset: 0 })}
          onScheduleTypeChange={(scheduleTypes) => updateJobsFilters({ scheduleTypes, offset: 0 })}
          onReset={resetJobsFilters}
        />

        {jobsQuery.isLoading && !jobsQuery.data ? (
          <div className="rounded-xl border bg-card py-8">
            <SpinnerBlock />
          </div>
        ) : jobs.length === 0 ? (
          <div className="rounded-xl border bg-card p-5">
            <EmptyState
              icon={Bot}
	              title={jobsFiltersApplied ? t("automations.jobs.emptyFilteredTitle") : t("automations.jobs.emptyTitle")}
	              description={
	                jobsFiltersApplied
	                  ? t("automations.jobs.emptyFilteredDescription")
	                  : t("automations.jobs.emptyDescription")
	              }
            />
          </div>
        ) : (
          <>
            <div className="rounded-xl border bg-card">
              <div className="relative overflow-x-auto">
                <Table className="min-w-[980px] table-fixed">
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
	                      <TableHead className="w-56 pl-4 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("automations.jobs.columns.name")}</TableHead>
	                      <TableHead className="w-32 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("automations.jobs.columns.type")}</TableHead>
	                      <TableHead className="w-56 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("automations.jobs.columns.schedule")}</TableHead>
	                      <TableHead className="w-40 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("dashboard.requests.columns.model")}</TableHead>
	                      <TableHead className="w-52 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("automations.jobs.columns.accounts")}</TableHead>
	                      <TableHead className="w-36 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("automations.jobs.columns.nextRun")}</TableHead>
	                      <TableHead className="w-28 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("automations.jobs.columns.lastStatus")}</TableHead>
	                      <TableHead className="w-36 pr-4 text-right text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("apiKeys.table.actions")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {jobs.map((job) => {
                      const nextRun = job.nextRunAt ? formatTimeLong(job.nextRunAt) : null;
                      const accountSummary = formatAccountsSummary(job.accountIds, accountDisplayIndex, job.accountScopeAll);
	                      const scheduleSummary = formatScheduleSummary(
	                        job.schedule.days,
	                        job.schedule.time,
	                        timeFormat,
	                        t,
	                      );
	                      const scheduleTimezoneLabel = formatTimezoneLabel(job.schedule.timezone, t);
	                      const thresholdLabel =
	                        job.schedule.thresholdMinutes > 0
	                          ? t("automations.schedule.spreadUpTo", { minutes: job.schedule.thresholdMinutes })
	                          : t("automations.schedule.immediateDispatch");
                      const modelLabel = formatModelLabel(job.model, job.reasoningEffort);
                      const shouldBlurJobAccountPrimary =
                        blurred &&
                        job.accountIds.length > 0 &&
                        (job.accountIds.length === 1
                          ? (accountBlurIndex.get(job.accountIds[0])?.primary ?? false)
                          : job.accountIds.some((accountId) => accountBlurIndex.get(accountId)?.any ?? false));
                      const shouldBlurJobAccountSecondary =
                        blurred &&
                        job.accountIds.length > 0 &&
                        (job.accountIds.length === 1
                          ? (accountBlurIndex.get(job.accountIds[0])?.secondary ?? false)
                          : job.accountIds.some((accountId) => accountBlurIndex.get(accountId)?.any ?? false));
                      return (
                        <TableRow key={job.id}>
                          <TableCell className="pl-4 align-middle">
                            <div className="space-y-0.5">
                              <div className="truncate text-sm font-medium" title={job.name}>{job.name}</div>
                              <div className="truncate text-xs text-muted-foreground" title={job.prompt}>
                                {job.prompt}
                              </div>
                            </div>
                          </TableCell>
	                          <TableCell className="align-middle text-xs text-muted-foreground">{formatTypeLabel(job.schedule.type, t)}</TableCell>
                          <TableCell className="align-middle text-xs">
                            <div className="space-y-0.5 leading-tight">
                              <div className="truncate text-foreground/95" title={scheduleSummary}>{scheduleSummary}</div>
                              <div className="truncate text-muted-foreground" title={scheduleTimezoneLabel}>{scheduleTimezoneLabel}</div>
                              <div className="truncate text-muted-foreground" title={thresholdLabel}>{thresholdLabel}</div>
                            </div>
                          </TableCell>
                          <TableCell className="truncate align-middle text-xs" title={modelLabel}>{modelLabel}</TableCell>
                          <TableCell className="align-middle text-xs">
                            <div className="space-y-0.5 leading-tight">
                              <div className="truncate text-foreground/95" title={accountSummary.title}>
                                {shouldBlurJobAccountPrimary ? (
                                  <span className="privacy-blur">{accountSummary.primary}</span>
                                ) : (
                                  accountSummary.primary
                                )}
                              </div>
                              {accountSummary.secondary ? (
                                <div className="truncate text-muted-foreground" title={accountSummary.title}>
                                  {shouldBlurJobAccountSecondary ? (
                                    <span className="privacy-blur">{accountSummary.secondary}</span>
                                  ) : (
                                    accountSummary.secondary
                                  )}
                                </div>
                              ) : null}
                            </div>
                          </TableCell>
                          <TableCell className="align-middle text-xs">
                            {nextRun ? (
                              <div className="space-y-0.5 leading-tight">
                                <div className="text-foreground/95">{nextRun.time}</div>
                                <div className="text-muted-foreground">{nextRun.date}</div>
                              </div>
                            ) : (
	                              <span className="text-muted-foreground">{t("common.states.disabled")}</span>
                            )}
                          </TableCell>
                          <TableCell className="align-middle text-xs">
                            {job.lastRun ? (
	                              <Badge variant={runStatusVariant((job.lastRun.effectiveStatus ?? job.lastRun.status) as AutomationRunStatus)}>
	                                {formatRunStatusLabel(
	                                  (job.lastRun.effectiveStatus ?? job.lastRun.status) as AutomationRunStatus,
	                                  job.lastRun.pendingAccounts,
	                                  t,
	                                )}
                              </Badge>
                            ) : (
                              <span className="text-muted-foreground">-</span>
                            )}
                          </TableCell>
                          <TableCell className="pr-4 text-right align-middle">
                            <div
                              className="flex items-center justify-end gap-1"
                              onClick={(event) => {
                                event.stopPropagation();
                              }}
                            >
                              <Switch
                                checked={job.enabled}
                                disabled={busy}
	                                aria-label={job.enabled ? t("automations.jobs.disableAria", { name: job.name }) : t("automations.jobs.enableAria", { name: job.name })}
	                                title={job.enabled ? t("automations.jobs.disableTitle") : t("automations.jobs.enableTitle")}
                                onCheckedChange={(checked) => {
                                  void updateMutation.mutateAsync({
                                    automationId: job.id,
                                    payload: { enabled: checked },
                                  });
                                }}
                              />
                              <Button
                                type="button"
                                size="icon"
                                variant="ghost"
                                className="h-8 w-8"
                                disabled={busy}
	                                aria-label={t("automations.jobs.editAria", { name: job.name })}
	                                title={t("automations.jobs.editTitle")}
                                onClick={() => {
                                  openEditDialog(job);
                                }}
                              >
                                <Pencil className="h-4 w-4" />
                              </Button>
                              <Button
                                type="button"
                                size="icon"
                                variant="ghost"
                                className="h-8 w-8"
                                disabled={busy}
	                                aria-label={t("automations.jobs.runNowAria", { name: job.name })}
	                                title={t("automations.jobs.runNowTitle")}
                                onClick={() => {
                                  runNowDialog.show(job);
                                }}
                              >
                                <Play className="h-4 w-4" />
                              </Button>
                              <Button
                                type="button"
                                size="icon"
                                variant="ghost"
                                className="h-8 w-8 text-destructive hover:text-destructive"
                                disabled={busy}
	                                aria-label={t("automations.jobs.deleteAria", { name: job.name })}
	                                title={t("automations.jobs.deleteTitle")}
                                onClick={() => deleteDialog.show(job)}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            </div>

            <div className="flex justify-end">
              <PaginationControls
                total={jobsTotal}
                limit={jobsFilters.limit}
                offset={jobsFilters.offset}
                hasMore={jobsHasMore}
                onLimitChange={(limit) => updateJobsFilters({ limit, offset: 0 })}
                onOffsetChange={(offset) => updateJobsFilters({ offset })}
              />
            </div>
          </>
        )}
      </section>

      <section className="space-y-3">
        <div>
	          <h3 className="text-sm font-semibold">{t("automations.runs.title")}</h3>
	          <p className="text-xs text-muted-foreground">{t("automations.runs.description")}</p>
        </div>

        <AutomationRunsFilters
          filters={runsFilters}
          accountOptions={runsAccountOptions}
          modelOptions={runsModelOptions}
          statusOptions={runsStatusOptions}
          triggerOptions={runsTriggerOptions}
          onSearchChange={(search) => updateRunsFilters({ search, offset: 0 })}
          onAccountChange={(accountIdsFilter) => updateRunsFilters({ accountIds: accountIdsFilter, offset: 0 })}
          onModelChange={(modelsFilter) => updateRunsFilters({ models: modelsFilter, offset: 0 })}
          onStatusChange={(statuses) => updateRunsFilters({ statuses, offset: 0 })}
          onTriggerChange={(triggers) => updateRunsFilters({ triggers, offset: 0 })}
          onReset={resetRunsFilters}
        />

        {runsQuery.isLoading && !runsQuery.data ? (
          <div className="rounded-xl border bg-card py-8">
            <SpinnerBlock />
          </div>
        ) : runs.length === 0 ? (
          <div className="rounded-xl border bg-card p-5">
            <EmptyState
              icon={Inbox}
	              title={runsFiltersApplied ? t("automations.runs.emptyFilteredTitle") : t("automations.runs.emptyTitle")}
	              description={
	                runsFiltersApplied
	                  ? t("automations.runs.emptyFilteredDescription")
	                  : t("automations.runs.emptyDescription")
	              }
            />
          </div>
        ) : (
          <>
            <div className="rounded-xl border bg-card">
              <div className="relative overflow-x-auto">
                <Table className="min-w-[980px] table-fixed">
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
	                      <TableHead className="w-24 pl-4 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("dashboard.requests.columns.status")}</TableHead>
	                      <TableHead className="w-56 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("automations.runs.columns.job")}</TableHead>
	                      <TableHead className="w-24 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("automations.runs.columns.trigger")}</TableHead>
	                      <TableHead className="w-36 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("automations.runs.columns.scheduledFor")}</TableHead>
	                      <TableHead className="w-36 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("automations.runs.columns.startedAt")}</TableHead>
	                      <TableHead className="w-36 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("automations.runs.columns.finishedAt")}</TableHead>
	                      <TableHead className="w-48 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("dashboard.requests.columns.account")}</TableHead>
	                      <TableHead className="w-64 pr-4 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("automations.runs.columns.error")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {runs.map((run) => {
                      const effectiveStatus = (run.effectiveStatus ?? run.status) as AutomationRunStatus;
                      const scheduled = formatTimeLong(run.scheduledFor);
                      const started = formatTimeLong(run.startedAt);
                      const finished = run.finishedAt ? formatTimeLong(run.finishedAt) : null;
                      const accountDisplay = resolveAccountDisplay(run.accountId, accountDisplayIndex);
                      const hasGroupedAccounts = (run.totalAccounts ?? 0) > 1;
                      const groupedPending = run.pendingAccounts ?? 0;
                      const groupedCompleted = run.completedAccounts ?? 0;
	                      const groupedAccountsLabel = t("automations.runs.groupedAccounts", { count: run.totalAccounts ?? 0 });
	                      const groupedAccountsSecondary =
	                        groupedPending > 0
	                          ? t("automations.runs.groupedProgress", { completed: groupedCompleted, pending: groupedPending })
	                          : t("automations.runs.groupedCycle");
                      const runAccountBlur = run.accountId ? accountBlurIndex.get(run.accountId) : null;
                      const shouldBlurRunAccountPrimary = !hasGroupedAccounts && blurred && !!runAccountBlur?.primary;
                      const shouldBlurRunAccountSecondary = !hasGroupedAccounts && blurred && !!runAccountBlur?.secondary;
                      const errorText = run.errorCode ? `${run.errorCode}${run.errorMessage ? `: ${run.errorMessage}` : ""}` : null;
                      const jobTitle = run.jobName || run.jobId;
                      const modelLabel = run.model ? formatModelLabel(run.model, run.reasoningEffort) : "-";

                      return (
                        <TableRow key={run.id}>
                          <TableCell className="pl-4 align-middle">
	                            <Badge variant={runStatusVariant(effectiveStatus)}>
	                              {formatRunStatusLabel(effectiveStatus, run.pendingAccounts, t)}
	                            </Badge>
                          </TableCell>
                          <TableCell className="align-middle text-xs">
                            <div className="space-y-0.5 leading-tight">
                              <div className="truncate text-foreground/95" title={jobTitle}>{jobTitle}</div>
                              <div className="truncate text-muted-foreground" title={modelLabel}>{modelLabel}</div>
                            </div>
                          </TableCell>
                          <TableCell className="align-middle text-xs text-muted-foreground">{run.trigger}</TableCell>
                          <TableCell className="align-middle text-xs">
                            <div className="space-y-0.5 leading-tight">
                              <div className="text-foreground/95">{scheduled.time}</div>
                              <div className="text-muted-foreground">{scheduled.date}</div>
                            </div>
                          </TableCell>
                          <TableCell className="align-middle text-xs">
                            <div className="space-y-0.5 leading-tight">
                              <div className="text-foreground/95">{started.time}</div>
                              <div className="text-muted-foreground">{started.date}</div>
                            </div>
                          </TableCell>
                          <TableCell className="align-middle text-xs">
                            {finished ? (
                              <div className="space-y-0.5 leading-tight">
                                <div className="text-foreground/95">{finished.time}</div>
                                <div className="text-muted-foreground">{finished.date}</div>
                              </div>
                            ) : (
                              <span className="text-muted-foreground">-</span>
                            )}
                          </TableCell>
                          <TableCell className="align-middle text-xs">
                            <div className="space-y-0.5 leading-tight">
                              <div
                                className="truncate text-foreground/95"
                                title={hasGroupedAccounts ? groupedAccountsLabel : accountDisplay.title}
                              >
                                {shouldBlurRunAccountPrimary ? (
                                  <span className="privacy-blur">{accountDisplay.primary}</span>
                                ) : hasGroupedAccounts ? (
                                  groupedAccountsLabel
                                ) : (
                                  accountDisplay.primary
                                )}
                              </div>
                              {(hasGroupedAccounts || accountDisplay.secondary) ? (
                                <div
                                  className="truncate text-muted-foreground"
                                  title={hasGroupedAccounts ? groupedAccountsSecondary : accountDisplay.title}
                                >
                                  {shouldBlurRunAccountSecondary ? (
                                    <span className="privacy-blur">{accountDisplay.secondary}</span>
                                  ) : hasGroupedAccounts ? (
                                    groupedAccountsSecondary
                                  ) : (
                                    accountDisplay.secondary
                                  )}
                                </div>
                              ) : null}
                            </div>
                          </TableCell>
                          <TableCell className="pr-4 align-middle text-xs text-muted-foreground whitespace-normal">
                            <div className="flex items-center justify-between gap-2">
                              <div className="min-w-0">
                                {errorText ? (
                                  <p className="line-clamp-2 break-words" title={errorText}>{errorText}</p>
                                ) : (
                                  "-"
                                )}
                              </div>
                              <Button
                                type="button"
                                size="icon"
                                variant="ghost"
                                className="h-7 w-7 shrink-0"
	                                aria-label={t("automations.runs.detailsAria", { id: run.id })}
	                                title={t("automations.runs.detailsTitle")}
                                onClick={() => setSelectedRunId(run.id)}
                              >
                                <Info className="h-4 w-4" />
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            </div>

            <div className="flex justify-end">
              <PaginationControls
                total={runsTotal}
                limit={runsFilters.limit}
                offset={runsFilters.offset}
                hasMore={runsHasMore}
                onLimitChange={(limit) => updateRunsFilters({ limit, offset: 0 })}
                onOffsetChange={(offset) => updateRunsFilters({ offset })}
              />
            </div>
          </>
        )}
      </section>

      <AutomationJobDialog
        open={createDialog.open}
        busy={busy}
        editingJob={editingJob}
        models={automationModels}
        modelsLoading={modelsLoading}
        onOpenChange={(open) => {
          createDialog.onOpenChange(open);
          if (!open) {
            setEditingJob(null);
          }
        }}
        onCreate={handleCreate}
        onUpdate={handleUpdate}
      />

      <ConfirmDialog
        open={deleteDialog.open}
	        title={t("automations.deleteDialog.title")}
	        description={t("automations.deleteDialog.description")}
	        confirmLabel={t("common.actions.delete")}
        onOpenChange={deleteDialog.onOpenChange}
        onConfirm={() => {
          if (!deleteDialog.data) {
            return;
          }
          void deleteMutation.mutateAsync(deleteDialog.data.id).finally(() => {
            deleteDialog.hide();
          });
        }}
      />

      <ConfirmDialog
        open={runNowDialog.open}
	        title={t("automations.runNowDialog.title")}
	        description={
	          runNowDialog.data
	            ? t("automations.runNowDialog.descriptionWithName", { name: runNowDialog.data.name })
	            : t("automations.runNowDialog.description")
	        }
	        confirmLabel={t("automations.runNowDialog.confirm")}
	        cancelLabel={t("common.cancel")}
        onOpenChange={runNowDialog.onOpenChange}
        onConfirm={() => {
          if (!runNowDialog.data) {
            return;
          }
          void runNowMutation.mutateAsync(runNowDialog.data.id).finally(() => {
            runNowDialog.hide();
          });
        }}
      />

      <RunDetailsDialog
        open={selectedRunId !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedRunId(null);
          }
        }}
        isLoading={runDetailsQuery.isLoading}
        data={runDetailsQuery.data}
        blurred={blurred}
        accountDisplayIndex={accountDisplayIndex}
        accountBlurIndex={accountBlurIndex}
      />
    </div>
  );
}
