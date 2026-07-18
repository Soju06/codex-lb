import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { AccountMultiSelect } from "@/features/api-keys/components/account-multi-select";
import type { ModelItem } from "@/features/api-keys/hooks/use-models";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import {
  formatScheduleTimeForInput,
  parseScheduleTimeInput,
  scheduleTimePlaceholder,
} from "@/features/automations/time-utils";
import { useTimeFormatStore, type TimeFormatPreference } from "@/hooks/use-time-format";
import { cn } from "@/lib/utils";
import type {
  AutomationCreateRequest,
  AutomationJob,
  AutomationReasoningEffort,
  AutomationScheduleDay,
  AutomationScheduleType,
  AutomationUpdateRequest,
} from "@/features/automations/schemas";

const AUTOMATION_TYPE_OPTIONS: Array<{ value: AutomationScheduleType; labelKey: string }> = [
  { value: "daily", labelKey: "automations.types.daily" },
];

const WEEKDAY_OPTIONS: Array<{ value: AutomationScheduleDay; shortLabel: string }> = [
  { value: "mon", shortLabel: "Mon" },
  { value: "tue", shortLabel: "Tue" },
  { value: "wed", shortLabel: "Wed" },
  { value: "thu", shortLabel: "Thu" },
  { value: "fri", shortLabel: "Fri" },
  { value: "sat", shortLabel: "Sat" },
  { value: "sun", shortLabel: "Sun" },
];

const DEFAULT_WEEKDAYS: AutomationScheduleDay[] = WEEKDAY_OPTIONS.map((option) => option.value);
const SERVER_DEFAULT_TIMEZONE = "server_default";
const DEFAULT_SCHEDULE_TIME = "05:00";
const DEFAULT_SCHEDULE_THRESHOLD_MINUTES = 0;
const MAX_SCHEDULE_THRESHOLD_MINUTES = 240;
const DEFAULT_REASONING_EFFORT_VALUE = "__default__";
const FALLBACK_REASONING_EFFORTS: AutomationReasoningEffort[] = ["low", "medium", "high", "xhigh", "max", "ultra"];
const REASONING_LABELS: Record<AutomationReasoningEffort, string> = {
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "XHigh",
  max: "Max",
  ultra: "Ultra",
};

type CreateFormField = "name" | "model" | "time" | "threshold" | "accounts";
type CreateFormErrors = Partial<Record<CreateFormField, string>>;

const FORM_FIELD_IDS: Record<
  CreateFormField | "type" | "timezone" | "days" | "prompt" | "reasoning" | "includePaused",
  string
> = {
  name: "automation-name",
  model: "automation-model",
  reasoning: "automation-reasoning",
  includePaused: "automation-include-paused",
  time: "automation-time",
  threshold: "automation-threshold",
  accounts: "automation-accounts",
  type: "automation-type",
  timezone: "automation-timezone",
  days: "automation-days",
  prompt: "automation-prompt",
};

type AutomationJobDialogProps = {
  open: boolean;
  busy: boolean;
  editingJob: AutomationJob | null;
  models: ModelItem[];
  modelsLoading: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (payload: AutomationCreateRequest) => Promise<void>;
  onUpdate: (automationId: string, payload: AutomationUpdateRequest) => Promise<void>;
};

type AutomationJobDialogFormProps = Omit<AutomationJobDialogProps, "open"> & {
  timeFormat: TimeFormatPreference;
};

function localTimezone(): string {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return timezone || "UTC";
}

function timezoneOptions(current: string): string[] {
  const local = localTimezone();
  const defaults = [
    SERVER_DEFAULT_TIMEZONE,
    local,
    "UTC",
    "Europe/Warsaw",
    "America/New_York",
    "America/Los_Angeles",
    "Asia/Tokyo",
  ];
  return Array.from(new Set([SERVER_DEFAULT_TIMEZONE, current, ...defaults].filter((value) => value.trim().length > 0)));
}

function automationAccountTargetsChanged(previous: string[], next: string[]): boolean {
  if (previous.length !== next.length) {
    return true;
  }
  const previousSet = new Set(previous);
  return next.some((accountId) => !previousSet.has(accountId));
}

function formatTimezoneLabel(value: string, t: ReturnType<typeof useTranslation>["t"]): string {
  return value === SERVER_DEFAULT_TIMEZONE ? t("automations.schedule.serverDefault") : value;
}

function validateCreateForm(values: {
  name: string;
  model: string;
  scheduleTimeValid: boolean;
  scheduleThresholdValid: boolean;
  availableAccountCount: number;
  t: ReturnType<typeof useTranslation>["t"];
}): CreateFormErrors {
  const errors: CreateFormErrors = {};
  if (values.name.trim().length === 0) {
    errors.name = values.t("automations.dialog.validation.nameRequired");
  }
  if (values.model.trim().length === 0) {
    errors.model = values.t("automations.dialog.validation.modelRequired");
  }
  if (!values.scheduleTimeValid) {
    errors.time = values.t("automations.dialog.validation.timeInvalid");
  }
  if (!values.scheduleThresholdValid) {
    errors.threshold = values.t("automations.dialog.validation.thresholdInvalid", {
      max: MAX_SCHEDULE_THRESHOLD_MINUTES,
    });
  }
  if (values.availableAccountCount <= 0) {
    errors.accounts = values.t("automations.dialog.validation.noAccounts");
  }
  return errors;
}

function parseThresholdInput(rawValue: string): { ok: true; value: number } | { ok: false } {
  const trimmed = rawValue.trim();
  if (trimmed.length === 0) {
    return { ok: false };
  }
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) {
    return { ok: false };
  }
  if (parsed < 0 || parsed > MAX_SCHEDULE_THRESHOLD_MINUTES) {
    return { ok: false };
  }
  return { ok: true, value: parsed };
}

function getAutomationJobDialogFormKey(editingJob: AutomationJob | null, timeFormat: TimeFormatPreference): string {
  if (editingJob === null) {
    return JSON.stringify(["create", timeFormat]);
  }
  return JSON.stringify([
    "edit",
    timeFormat,
    editingJob.id,
    editingJob.name,
    editingJob.enabled,
    editingJob.includePausedAccounts,
    editingJob.schedule.type,
    editingJob.schedule.time,
    editingJob.schedule.timezone,
    editingJob.schedule.thresholdMinutes,
    editingJob.schedule.days,
    editingJob.model,
    editingJob.reasoningEffort ?? null,
    editingJob.prompt,
    editingJob.accountScopeAll,
    editingJob.accountIds,
  ]);
}

export function AutomationJobDialog({
  open,
  onOpenChange,
  ...formProps
}: AutomationJobDialogProps) {
  const timeFormat = useTimeFormatStore((state) => state.timeFormat);
  const formKey = getAutomationJobDialogFormKey(formProps.editingJob, timeFormat);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <AutomationJobDialogForm
          key={formKey}
          {...formProps}
          onOpenChange={onOpenChange}
          timeFormat={timeFormat}
        />
      ) : null}
    </Dialog>
  );
}

function AutomationJobDialogForm({
  busy,
  editingJob,
  models,
  modelsLoading,
  onOpenChange,
  onCreate,
  onUpdate,
  timeFormat,
}: AutomationJobDialogFormProps) {
  const { t } = useTranslation();
  const isEditing = editingJob !== null;
  const { accountsQuery } = useAccounts();
  const initialName = editingJob?.name ?? "";
  const initialScheduleTime = editingJob?.schedule.time ?? DEFAULT_SCHEDULE_TIME;
  const initialScheduleThreshold = editingJob?.schedule.thresholdMinutes ?? DEFAULT_SCHEDULE_THRESHOLD_MINUTES;

  const [nameDefaultValue] = useState(initialName);
  const [nameValidationValue, setNameValidationValue] = useState(initialName);
  const [nameHasValue, setNameHasValue] = useState(initialName.trim().length > 0);
  const nameInputVersion = 0;
  const [automationType, setAutomationType] = useState<AutomationScheduleType>(editingJob?.schedule.type ?? "daily");
  const [scheduleTimeDefaultValue] = useState(formatScheduleTimeForInput(initialScheduleTime, timeFormat));
  const scheduleTimeInputVersion = 0;
  const [scheduleTimeIsValid, setScheduleTimeIsValid] = useState(true);
  const [scheduleThresholdDefaultValue] = useState(String(initialScheduleThreshold));
  const scheduleThresholdInputVersion = 0;
  const [scheduleThresholdIsValid, setScheduleThresholdIsValid] = useState(true);
  const [includePausedAccounts, setIncludePausedAccounts] = useState(editingJob?.includePausedAccounts ?? false);
  const [scheduleTimezone, setScheduleTimezone] = useState(editingJob?.schedule.timezone ?? SERVER_DEFAULT_TIMEZONE);
  const [scheduleDays, setScheduleDays] = useState<AutomationScheduleDay[]>(() => [
    ...(editingJob?.schedule.days ?? DEFAULT_WEEKDAYS),
  ]);
  const [model, setModel] = useState(editingJob?.model ?? "");
  const [reasoningEffortValue, setReasoningEffortValue] = useState(
    editingJob?.reasoningEffort ?? DEFAULT_REASONING_EFFORT_VALUE,
  );
  const [reasoningEffortTouched, setReasoningEffortTouched] = useState(false);
  const [promptDefaultValue] = useState(editingJob?.prompt ?? "ping");
  const promptInputVersion = 0;
  const [accountIds, setAccountIds] = useState<string[]>(() => [...(editingJob?.accountIds ?? [])]);
  const nameRef = useRef<HTMLInputElement | null>(null);
  const timeRef = useRef<HTMLInputElement | null>(null);
  const thresholdRef = useRef<HTMLInputElement | null>(null);
  const promptRef = useRef<HTMLTextAreaElement | null>(null);
  const [touched, setTouched] = useState<Record<CreateFormField, boolean>>({
    name: false,
    model: false,
    time: false,
    threshold: false,
    accounts: false,
  });
  const [submitAttempted, setSubmitAttempted] = useState(false);

  const availableAccountCount = accountsQuery.data?.length ?? 0;
  const selectedModel = model || models[0]?.id || "";
  const selectedModelMetadata = useMemo(
    () => models.find((entry) => entry.id === selectedModel) ?? null,
    [models, selectedModel],
  );
  const availableReasoningEfforts = useMemo(() => {
    const fromModel = selectedModelMetadata?.supportedReasoningEfforts;
    if (fromModel != null) {
      return fromModel;
    }
    return FALLBACK_REASONING_EFFORTS;
  }, [selectedModelMetadata]);
  const effectiveReasoningEffortValue =
    reasoningEffortValue === DEFAULT_REASONING_EFFORT_VALUE ||
    availableReasoningEfforts.includes(reasoningEffortValue as AutomationReasoningEffort)
      ? reasoningEffortValue
      : DEFAULT_REASONING_EFFORT_VALUE;
  const selectedReasoningEffort =
    effectiveReasoningEffortValue === DEFAULT_REASONING_EFFORT_VALUE
      ? null
      : (effectiveReasoningEffortValue as AutomationReasoningEffort);
  const shouldPersistReasoningEffortOnUpdate =
    editingJob !== null &&
    selectedModel !== editingJob.model &&
    selectedReasoningEffort !== (editingJob.reasoningEffort ?? null);
  const shouldClearStoredUnsupportedReasoningEffortOnUpdate =
    editingJob !== null &&
    selectedModelMetadata !== null &&
    selectedModel === editingJob.model &&
    selectedReasoningEffort === null &&
    editingJob.reasoningEffort != null &&
    !availableReasoningEfforts.includes(editingJob.reasoningEffort);
  const timezoneChoices = useMemo(() => timezoneOptions(scheduleTimezone), [scheduleTimezone]);
  const modelSelectItems = useMemo(
    () =>
      models.map((entry) => (
        <SelectItem key={entry.id} value={entry.id}>
          {entry.id}
        </SelectItem>
      )),
    [models],
  );
  const reasoningSelectItems = useMemo(
    () =>
      availableReasoningEfforts.map((effort) => (
        <SelectItem key={effort} value={effort}>
          {t(`common.reasoning.${effort}`, { defaultValue: REASONING_LABELS[effort] ?? effort })}
        </SelectItem>
      )),
    [availableReasoningEfforts, t],
  );
  const createFormErrors = useMemo(
    () =>
      validateCreateForm({
        name: nameValidationValue,
        model: selectedModel,
        scheduleTimeValid: scheduleTimeIsValid,
        scheduleThresholdValid: scheduleThresholdIsValid,
        availableAccountCount,
        t,
      }),
    [availableAccountCount, nameValidationValue, scheduleThresholdIsValid, scheduleTimeIsValid, selectedModel, t],
  );

  const canSubmit =
    nameHasValue &&
    Object.entries(createFormErrors).filter(([field]) => field !== "name").length === 0 &&
    scheduleDays.length > 0;
  const showFieldError = (field: CreateFormField): string | null => {
    if (!(submitAttempted || touched[field])) {
      return null;
    }
    return createFormErrors[field] ?? null;
  };

  const handleSubmit = async () => {
    setSubmitAttempted(true);
    const nameValue = (nameRef.current?.value ?? nameDefaultValue).trim();
    setNameValidationValue(nameValue);
    setNameHasValue(nameValue.length > 0);
    setTouched((current) => ({ ...current, name: true, time: true, threshold: true }));

    const scheduleTimeInput = timeRef.current?.value ?? scheduleTimeDefaultValue;
    const parsedTime = parseScheduleTimeInput(scheduleTimeInput, timeFormat);
    if (parsedTime.ok) {
      const normalizedTime = formatScheduleTimeForInput(parsedTime.value, timeFormat);
      if (timeRef.current) {
        timeRef.current.value = normalizedTime;
      }
      setScheduleTimeIsValid(true);
    } else {
      setScheduleTimeIsValid(false);
    }

    const scheduleThresholdInput = thresholdRef.current?.value ?? scheduleThresholdDefaultValue;
    const parsedThreshold = parseThresholdInput(scheduleThresholdInput);
    if (parsedThreshold.ok) {
      if (thresholdRef.current) {
        thresholdRef.current.value = String(parsedThreshold.value);
      }
      setScheduleThresholdIsValid(true);
    } else {
      setScheduleThresholdIsValid(false);
    }

    const submitErrors = validateCreateForm({
      name: nameValue,
      model: selectedModel,
      scheduleTimeValid: parsedTime.ok,
      scheduleThresholdValid: parsedThreshold.ok,
      availableAccountCount,
      t,
    });
    if (Object.keys(submitErrors).length > 0 || scheduleDays.length === 0 || !parsedTime.ok || !parsedThreshold.ok) {
      return;
    }
    const promptText = (promptRef.current?.value ?? promptDefaultValue).trim() || "ping";

    const payloadBase: Omit<AutomationCreateRequest, "accountIds"> | AutomationUpdateRequest = {
      name: nameValue,
      includePausedAccounts,
      schedule: {
        type: automationType,
        time: parsedTime.value,
        timezone: scheduleTimezone.trim(),
        thresholdMinutes: parsedThreshold.value,
        days: scheduleDays,
      },
      model: selectedModel.trim(),
      prompt: promptText,
    };

    try {
      if (editingJob) {
        const nextAccountScopeAll = accountIds.length === 0;
        const currentAccountScopeAll = editingJob.accountScopeAll ?? editingJob.accountIds.length === 0;
        const targetPatch =
          automationAccountTargetsChanged(editingJob.accountIds, accountIds) ||
          currentAccountScopeAll !== nextAccountScopeAll
            ? { accountIds }
            : {};
        const updatePayload: AutomationUpdateRequest =
          reasoningEffortTouched ||
          shouldPersistReasoningEffortOnUpdate ||
          shouldClearStoredUnsupportedReasoningEffortOnUpdate
            ? {
                ...(payloadBase as AutomationUpdateRequest),
                ...targetPatch,
                reasoningEffort: selectedReasoningEffort,
              }
            : {
                ...(payloadBase as AutomationUpdateRequest),
                ...targetPatch,
              };
        await onUpdate(editingJob.id, updatePayload);
      } else {
        await onCreate({
          ...(payloadBase as AutomationCreateRequest),
          accountIds,
          reasoningEffort: selectedReasoningEffort,
          enabled: true,
        });
      }
      onOpenChange(false);
    } catch {
      return;
    }
  };

  return (
    <DialogContent className="flex max-h-[calc(100vh-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl">
        <DialogHeader className="px-6 pt-6 pb-2 pr-12">
	          <DialogTitle>{isEditing ? t("automations.dialog.editTitle") : t("automations.dialog.createTitle")}</DialogTitle>
	          <DialogDescription>
	            {isEditing
	              ? t("automations.dialog.editDescription")
	              : t("automations.dialog.createDescription")}
	          </DialogDescription>
        </DialogHeader>

        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(event) => {
            event.preventDefault();
            void handleSubmit();
          }}
        >
          <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-6 pb-4">
            <section className="space-y-3">
              <div className="space-y-0.5">
	                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{t("automations.dialog.sections.basics")}</h4>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5 sm:col-span-2">
	                  <label htmlFor={FORM_FIELD_IDS.name} className="text-sm font-medium">{t("apiKeys.table.name")}</label>
                  <Input
                    key={`name-${nameInputVersion}`}
                    ref={nameRef}
                    id={FORM_FIELD_IDS.name}
	                    placeholder={t("automations.dialog.placeholders.name")}
                    defaultValue={nameDefaultValue}
                    aria-invalid={showFieldError("name") ? true : undefined}
                    aria-describedby={showFieldError("name") ? `${FORM_FIELD_IDS.name}-error` : undefined}
                    onBlur={() => {
                      setTouched((current) => ({ ...current, name: true }));
                      const normalizedName = (nameRef.current?.value ?? "").trim();
                      setNameValidationValue(normalizedName);
                      setNameHasValue(normalizedName.length > 0);
                    }}
                    onInput={(event) => {
                      const normalizedName = event.currentTarget.value.trim();
                      setNameHasValue(normalizedName.length > 0);
                    }}
                  />
                  {showFieldError("name") ? (
                    <p id={`${FORM_FIELD_IDS.name}-error`} className="text-xs text-destructive">{showFieldError("name")}</p>
                  ) : null}
                </div>

                <div className="space-y-1.5">
	                  <label htmlFor={FORM_FIELD_IDS.type} className="text-sm font-medium">{t("automations.jobs.columns.type")}</label>
                  <Select value={automationType} onValueChange={(value) => setAutomationType(value as AutomationScheduleType)}>
                    <SelectTrigger id={FORM_FIELD_IDS.type}>
	                      <SelectValue placeholder={t("automations.dialog.placeholders.type")} />
                    </SelectTrigger>
                    <SelectContent>
                      {AUTOMATION_TYPE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
	                          {t(option.labelKey)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5">
	                  <label htmlFor={FORM_FIELD_IDS.model} className="text-sm font-medium">{t("dashboard.requests.columns.model")}</label>
                  <Select
                    value={selectedModel}
                    onValueChange={(value) => {
                      setModel(value);
                      setTouched((current) => ({ ...current, model: true }));
                    }}
                  >
                    <SelectTrigger
                      id={FORM_FIELD_IDS.model}
                      aria-invalid={showFieldError("model") ? true : undefined}
                      aria-describedby={showFieldError("model") ? `${FORM_FIELD_IDS.model}-error` : undefined}
                    >
	                      <SelectValue placeholder={t("automations.dialog.placeholders.model")} />
                    </SelectTrigger>
                    <SelectContent align="start">
                      {modelSelectItems}
                    </SelectContent>
                  </Select>
                  {!modelsLoading && models.length === 0 ? (
	                    <p className="text-xs text-destructive">{t("automations.dialog.noModels")}</p>
                  ) : null}
                  {showFieldError("model") ? (
                    <p id={`${FORM_FIELD_IDS.model}-error`} className="text-xs text-destructive">{showFieldError("model")}</p>
                  ) : null}
                </div>

                <div className="space-y-1.5 sm:col-span-2">
		                  <label htmlFor={FORM_FIELD_IDS.reasoning} className="text-sm font-medium">{t("automations.dialog.reasoningEffort")}</label>
                  <Select
                    value={effectiveReasoningEffortValue}
                    onValueChange={(value) => {
                      setReasoningEffortValue(value);
                      setReasoningEffortTouched(true);
                    }}
                  >
                    <SelectTrigger id={FORM_FIELD_IDS.reasoning}>
	                      <SelectValue placeholder={t("automations.dialog.modelDefault")} />
                    </SelectTrigger>
                    <SelectContent align="start">
                      <SelectItem value={DEFAULT_REASONING_EFFORT_VALUE}>
	                        {t("automations.dialog.modelDefault")}
	                        {selectedModelMetadata?.defaultReasoningEffort
	                          ? ` (${t(`common.reasoning.${selectedModelMetadata.defaultReasoningEffort}`, { defaultValue: REASONING_LABELS[selectedModelMetadata.defaultReasoningEffort] ?? selectedModelMetadata.defaultReasoningEffort })})`
	                          : ""}
                      </SelectItem>
                      {reasoningSelectItems}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
	                    {t("automations.dialog.reasoningHint")}
                  </p>
                </div>
              </div>
            </section>

            <section className="space-y-3">
              <div className="space-y-0.5">
	                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{t("automations.dialog.sections.schedule")}</h4>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
	                  <label htmlFor={FORM_FIELD_IDS.time} className="text-sm font-medium">{t("dashboard.requests.columns.time")}</label>
                  <Input
                    key={`time-${scheduleTimeInputVersion}`}
                    ref={timeRef}
                    id={FORM_FIELD_IDS.time}
                    type="text"
                    inputMode="text"
                    autoComplete="off"
                    defaultValue={scheduleTimeDefaultValue}
                    placeholder={scheduleTimePlaceholder(timeFormat)}
                    aria-invalid={showFieldError("time") ? true : undefined}
                    aria-describedby={showFieldError("time") ? `${FORM_FIELD_IDS.time}-error` : `${FORM_FIELD_IDS.time}-hint`}
                    onBlur={() => {
                      setTouched((current) => ({ ...current, time: true }));
                      const nextValue = timeRef.current?.value ?? scheduleTimeDefaultValue;
                      const parsed = parseScheduleTimeInput(nextValue, timeFormat);
                      if (!parsed.ok) {
                        setScheduleTimeIsValid(false);
                        return;
                      }
                      const normalized = formatScheduleTimeForInput(parsed.value, timeFormat);
                      if (timeRef.current) {
                        timeRef.current.value = normalized;
                      }
                      setScheduleTimeIsValid(true);
                    }}
                  />
                  <p id={`${FORM_FIELD_IDS.time}-hint`} className="text-xs text-muted-foreground">
	                    {timeFormat === "24h" ? t("automations.dialog.timeHint24h") : t("automations.dialog.timeHint12h")}
                  </p>
                  {showFieldError("time") ? (
                    <p id={`${FORM_FIELD_IDS.time}-error`} className="text-xs text-destructive">{showFieldError("time")}</p>
                  ) : null}
                </div>

                <div className="space-y-1.5">
	                  <label htmlFor={FORM_FIELD_IDS.timezone} className="text-sm font-medium">{t("quotaPlanner.fields.timezone")}</label>
                  <Select value={scheduleTimezone} onValueChange={setScheduleTimezone}>
                    <SelectTrigger id={FORM_FIELD_IDS.timezone}>
	                      <SelectValue placeholder={t("automations.dialog.placeholders.timezone")}>
	                        {formatTimezoneLabel(scheduleTimezone, t)}
	                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {timezoneChoices.map((timezone) => (
                        <SelectItem key={timezone} value={timezone}>
	                          {formatTimezoneLabel(timezone, t)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5 sm:col-span-2">
	                  <label htmlFor={FORM_FIELD_IDS.threshold} className="text-sm font-medium">{t("automations.dialog.thresholdMinutes")}</label>
                  <Input
                    key={`threshold-${scheduleThresholdInputVersion}`}
                    ref={thresholdRef}
                    id={FORM_FIELD_IDS.threshold}
                    type="number"
                    min={0}
                    max={MAX_SCHEDULE_THRESHOLD_MINUTES}
                    step={1}
                    defaultValue={scheduleThresholdDefaultValue}
                    aria-invalid={showFieldError("threshold") ? true : undefined}
                    aria-describedby={
                      showFieldError("threshold")
                        ? `${FORM_FIELD_IDS.threshold}-error`
                        : `${FORM_FIELD_IDS.threshold}-hint`
                    }
                    onBlur={() => {
                      setTouched((current) => ({ ...current, threshold: true }));
                      const nextValue = thresholdRef.current?.value ?? scheduleThresholdDefaultValue;
                      const parsed = parseThresholdInput(nextValue);
                      if (!parsed.ok) {
                        setScheduleThresholdIsValid(false);
                        return;
                      }
                      if (thresholdRef.current) {
                        thresholdRef.current.value = String(parsed.value);
                      }
                      setScheduleThresholdIsValid(true);
                    }}
                  />
                  <p id={`${FORM_FIELD_IDS.threshold}-hint`} className="text-xs text-muted-foreground">
	                    {t("automations.dialog.thresholdHint")}
                  </p>
                  {showFieldError("threshold") ? (
                    <p id={`${FORM_FIELD_IDS.threshold}-error`} className="text-xs text-destructive">
                      {showFieldError("threshold")}
                    </p>
                  ) : null}
                </div>

                <div className="space-y-1.5 sm:col-span-2">
                  <div className="border-border/70 bg-muted/10 flex items-center justify-between rounded-md border px-3 py-2.5">
                    <div className="space-y-0.5">
                      <label htmlFor={FORM_FIELD_IDS.includePaused} className="text-sm font-medium">
	                        {t("automations.dialog.includePaused")}
                      </label>
                      <p className="text-xs text-muted-foreground">
	                        {t("automations.dialog.includePausedDescription")}
                      </p>
                    </div>
                    <Switch
                      id={FORM_FIELD_IDS.includePaused}
                      checked={includePausedAccounts}
                      onCheckedChange={setIncludePausedAccounts}
	                      aria-label={t("automations.dialog.includePaused")}
                    />
                  </div>
                </div>

                <div className="space-y-1.5 sm:col-span-2">
	                  <label htmlFor={FORM_FIELD_IDS.days} className="text-sm font-medium">{t("automations.dialog.daysOfWeek")}</label>
	                  <div id={FORM_FIELD_IDS.days} role="group" aria-label={t("automations.dialog.daysOfWeek")} className="grid grid-cols-4 gap-1.5 sm:grid-cols-7">
                    {WEEKDAY_OPTIONS.map((option) => {
                      const selected = scheduleDays.includes(option.value);
                      return (
                        <Button
                          key={option.value}
                          type="button"
                          size="sm"
                          variant="outline"
                          aria-pressed={selected}
                          className={cn(
                            "font-medium",
                            selected
                              ? "border-primary/35 bg-primary/15 text-primary hover:bg-primary/20 hover:text-primary"
                              : "border-border/70 bg-background text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                          )}
                          onClick={() => toggleScheduleDay(option.value)}
                        >
	                          {t(`settings.routing.workingDays.days.${option.value}`, { defaultValue: option.shortLabel })}
                        </Button>
                      );
                    })}
                  </div>
	                  <p className="text-xs text-muted-foreground">{t("automations.dialog.daysHint")}</p>
                </div>
              </div>
            </section>

            <section className="space-y-3">
              <div className="space-y-0.5">
	                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{t("automations.dialog.sections.execution")}</h4>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5 sm:col-span-2">
	                  <label htmlFor={FORM_FIELD_IDS.prompt} className="text-sm font-medium">{t("automations.dialog.prompt")}</label>
                  <textarea
                    key={`prompt-${promptInputVersion}`}
                    ref={promptRef}
                    id={FORM_FIELD_IDS.prompt}
                    className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive min-h-24 w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px]"
	                    placeholder={t("automations.dialog.placeholders.prompt")}
                    defaultValue={promptDefaultValue}
                  />
                </div>

                <div className="space-y-1.5 sm:col-span-2">
	                  <label htmlFor={FORM_FIELD_IDS.accounts} className="text-sm font-medium">{t("automations.jobs.columns.accounts")}</label>
                  <AccountMultiSelect
                    value={accountIds}
                    onChange={(value) => {
                      setAccountIds(value);
                      setTouched((current) => ({ ...current, accounts: true }));
                    }}
	                    placeholder={t("apiKeys.accountSelect.all")}
                    triggerId={FORM_FIELD_IDS.accounts}
                    ariaInvalid={showFieldError("accounts") ? true : undefined}
                    ariaDescribedBy={showFieldError("accounts") ? `${FORM_FIELD_IDS.accounts}-error` : undefined}
                    allowPausedAccounts={includePausedAccounts}
                  />
                  {showFieldError("accounts") ? (
                    <p id={`${FORM_FIELD_IDS.accounts}-error`} className="text-xs text-destructive">{showFieldError("accounts")}</p>
                  ) : null}
                </div>
              </div>
            </section>

            {submitAttempted && !canSubmit ? (
              <p className="text-xs text-muted-foreground">
	                {isEditing
	                  ? t("automations.dialog.resolveToSave")
	                  : t("automations.dialog.resolveToCreate")}
              </p>
            ) : null}
          </div>

          <DialogFooter className="border-t px-6 py-4">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
	              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={busy || modelsLoading || !canSubmit}>
	              {isEditing ? t("automations.dialog.saveChanges") : t("automations.dialog.createAction")}
            </Button>
          </DialogFooter>
        </form>
    </DialogContent>
  );

  function toggleScheduleDay(day: AutomationScheduleDay) {
    setScheduleDays((current) => {
      if (current.includes(day)) {
        if (current.length === 1) {
          return current;
        }
        return current.filter((entry) => entry !== day);
      }
      return WEEKDAY_OPTIONS.map((option) => option.value).filter((option) =>
        option === day ? true : current.includes(option),
      );
    });
  }
}
