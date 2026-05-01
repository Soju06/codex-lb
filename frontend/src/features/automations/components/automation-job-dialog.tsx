import { useEffect, useMemo, useRef, useState } from "react";

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
import { useTimeFormatStore } from "@/hooks/use-time-format";
import { cn } from "@/lib/utils";
import type {
  AutomationCreateRequest,
  AutomationJob,
  AutomationReasoningEffort,
  AutomationScheduleDay,
  AutomationScheduleType,
  AutomationUpdateRequest,
} from "@/features/automations/schemas";

const AUTOMATION_TYPE_OPTIONS: Array<{ value: AutomationScheduleType; label: string }> = [
  { value: "daily", label: "Daily refresh" },
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
const FALLBACK_REASONING_EFFORTS: AutomationReasoningEffort[] = ["low", "medium", "high", "xhigh"];
const REASONING_LABELS: Record<AutomationReasoningEffort, string> = {
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "XHigh",
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

function formatTimezoneLabel(value: string): string {
  return value === SERVER_DEFAULT_TIMEZONE ? "Server default" : value;
}

function validateCreateForm(values: {
  name: string;
  model: string;
  scheduleTimeValid: boolean;
  scheduleThresholdValid: boolean;
  availableAccountCount: number;
}): CreateFormErrors {
  const errors: CreateFormErrors = {};
  if (values.name.trim().length === 0) {
    errors.name = "Name is required.";
  }
  if (values.model.trim().length === 0) {
    errors.model = "Model is required.";
  }
  if (!values.scheduleTimeValid) {
    errors.time = "Enter a valid time value.";
  }
  if (!values.scheduleThresholdValid) {
    errors.threshold = `Threshold must be between 0 and ${MAX_SCHEDULE_THRESHOLD_MINUTES} minutes.`;
  }
  if (values.availableAccountCount <= 0) {
    errors.accounts = "No accounts available. Add at least one account.";
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

export function AutomationJobDialog({
  open,
  busy,
  editingJob,
  models,
  modelsLoading,
  onOpenChange,
  onCreate,
  onUpdate,
}: AutomationJobDialogProps) {
  const isEditing = editingJob !== null;
  const timeFormat = useTimeFormatStore((state) => state.timeFormat);
  const { accountsQuery } = useAccounts();

  const [nameDefaultValue, setNameDefaultValue] = useState("");
  const [nameValidationValue, setNameValidationValue] = useState("");
  const [nameHasValue, setNameHasValue] = useState(false);
  const [nameInputVersion, setNameInputVersion] = useState(0);
  const [automationType, setAutomationType] = useState<AutomationScheduleType>("daily");
  const [scheduleTimeValue, setScheduleTimeValue] = useState(DEFAULT_SCHEDULE_TIME);
  const [scheduleTimeDefaultValue, setScheduleTimeDefaultValue] = useState(
    formatScheduleTimeForInput(DEFAULT_SCHEDULE_TIME, timeFormat),
  );
  const [scheduleTimeInputVersion, setScheduleTimeInputVersion] = useState(0);
  const [scheduleTimeIsValid, setScheduleTimeIsValid] = useState(true);
  const [scheduleThresholdDefaultValue, setScheduleThresholdDefaultValue] = useState(
    String(DEFAULT_SCHEDULE_THRESHOLD_MINUTES),
  );
  const [scheduleThresholdInputVersion, setScheduleThresholdInputVersion] = useState(0);
  const [scheduleThresholdIsValid, setScheduleThresholdIsValid] = useState(true);
  const [includePausedAccounts, setIncludePausedAccounts] = useState(false);
  const [scheduleTimezone, setScheduleTimezone] = useState(SERVER_DEFAULT_TIMEZONE);
  const [scheduleDays, setScheduleDays] = useState<AutomationScheduleDay[]>([...DEFAULT_WEEKDAYS]);
  const [model, setModel] = useState("");
  const [reasoningEffortValue, setReasoningEffortValue] = useState(DEFAULT_REASONING_EFFORT_VALUE);
  const [reasoningEffortTouched, setReasoningEffortTouched] = useState(false);
  const [promptDefaultValue, setPromptDefaultValue] = useState("ping");
  const [promptInputVersion, setPromptInputVersion] = useState(0);
  const [accountIds, setAccountIds] = useState<string[]>([]);
  const nameRef = useRef<HTMLInputElement | null>(null);
  const timeRef = useRef<HTMLInputElement | null>(null);
  const thresholdRef = useRef<HTMLInputElement | null>(null);
  const promptRef = useRef<HTMLTextAreaElement | null>(null);
  const reasoningEffortProgrammaticValueRef = useRef<string | null>(null);
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
  const selectedReasoningEffort =
    reasoningEffortValue === DEFAULT_REASONING_EFFORT_VALUE
      ? null
      : (reasoningEffortValue as AutomationReasoningEffort);
  const shouldPersistReasoningEffortOnUpdate =
    editingJob !== null &&
    selectedModel !== editingJob.model &&
    selectedReasoningEffort !== (editingJob.reasoningEffort ?? null);
  const shouldClearStoredUnsupportedReasoningEffortOnUpdate =
    editingJob !== null &&
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
          {REASONING_LABELS[effort] ?? effort}
        </SelectItem>
      )),
    [availableReasoningEfforts],
  );
  const createFormErrors = useMemo(
    () =>
      validateCreateForm({
        name: nameValidationValue,
        model: selectedModel,
        scheduleTimeValid: scheduleTimeIsValid,
        scheduleThresholdValid: scheduleThresholdIsValid,
        availableAccountCount,
      }),
    [availableAccountCount, nameValidationValue, scheduleThresholdIsValid, scheduleTimeIsValid, selectedModel],
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

  const setReasoningEffortValueProgrammatically = (value: string) => {
    reasoningEffortProgrammaticValueRef.current = value;
    setReasoningEffortValue(value);
  };

  useEffect(() => {
    if (!open) {
      return;
    }
    if (editingJob) {
      setNameDefaultValue(editingJob.name);
      setNameValidationValue(editingJob.name);
      setNameHasValue(editingJob.name.trim().length > 0);
      setNameInputVersion((value) => value + 1);
      setAutomationType(editingJob.schedule.type);
      setScheduleTimeValue(editingJob.schedule.time);
      setScheduleTimeDefaultValue(formatScheduleTimeForInput(editingJob.schedule.time, timeFormat));
      setScheduleTimeInputVersion((value) => value + 1);
      setScheduleTimeIsValid(true);
      const threshold = editingJob.schedule.thresholdMinutes ?? DEFAULT_SCHEDULE_THRESHOLD_MINUTES;
      setScheduleThresholdDefaultValue(String(threshold));
      setScheduleThresholdInputVersion((value) => value + 1);
      setScheduleThresholdIsValid(true);
      setIncludePausedAccounts(editingJob.includePausedAccounts ?? false);
      setScheduleTimezone(editingJob.schedule.timezone);
      setScheduleDays([...editingJob.schedule.days]);
      setModel(editingJob.model);
      setReasoningEffortValueProgrammatically(editingJob.reasoningEffort ?? DEFAULT_REASONING_EFFORT_VALUE);
      setReasoningEffortTouched(false);
      setPromptDefaultValue(editingJob.prompt);
      setPromptInputVersion((value) => value + 1);
      setAccountIds([...editingJob.accountIds]);
    } else {
      setNameDefaultValue("");
      setNameValidationValue("");
      setNameHasValue(false);
      setNameInputVersion((value) => value + 1);
      setAutomationType("daily");
      setScheduleTimeValue(DEFAULT_SCHEDULE_TIME);
      setScheduleTimeDefaultValue(formatScheduleTimeForInput(DEFAULT_SCHEDULE_TIME, timeFormat));
      setScheduleTimeInputVersion((value) => value + 1);
      setScheduleTimeIsValid(true);
      setScheduleThresholdDefaultValue(String(DEFAULT_SCHEDULE_THRESHOLD_MINUTES));
      setScheduleThresholdInputVersion((value) => value + 1);
      setScheduleThresholdIsValid(true);
      setIncludePausedAccounts(false);
      setScheduleTimezone(SERVER_DEFAULT_TIMEZONE);
      setScheduleDays([...DEFAULT_WEEKDAYS]);
      setModel("");
      setReasoningEffortValueProgrammatically(DEFAULT_REASONING_EFFORT_VALUE);
      setReasoningEffortTouched(false);
      setPromptDefaultValue("ping");
      setPromptInputVersion((value) => value + 1);
      setAccountIds([]);
    }
    setTouched({ name: false, model: false, time: false, threshold: false, accounts: false });
    setSubmitAttempted(false);
  }, [editingJob, open, timeFormat]);

  useEffect(() => {
    if (!open) {
      return;
    }
    setScheduleTimeDefaultValue(formatScheduleTimeForInput(scheduleTimeValue, timeFormat));
    setScheduleTimeInputVersion((value) => value + 1);
  }, [open, scheduleTimeValue, timeFormat]);

  useEffect(() => {
    if (reasoningEffortValue === DEFAULT_REASONING_EFFORT_VALUE) {
      return;
    }
    if (!availableReasoningEfforts.includes(reasoningEffortValue as AutomationReasoningEffort)) {
      setReasoningEffortValueProgrammatically(DEFAULT_REASONING_EFFORT_VALUE);
    }
  }, [availableReasoningEfforts, reasoningEffortValue]);

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
      setScheduleTimeValue(parsedTime.value);
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
    });
    if (Object.keys(submitErrors).length > 0 || scheduleDays.length === 0 || !parsedTime.ok || !parsedThreshold.ok) {
      return;
    }
    const promptText = (promptRef.current?.value ?? promptDefaultValue).trim() || "ping";

    const payloadBase: AutomationCreateRequest | AutomationUpdateRequest = {
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
      accountIds,
    };

    try {
      if (editingJob) {
        const updatePayload: AutomationUpdateRequest =
          reasoningEffortTouched ||
          shouldPersistReasoningEffortOnUpdate ||
          shouldClearStoredUnsupportedReasoningEffortOnUpdate
            ? {
                ...(payloadBase as AutomationUpdateRequest),
                reasoningEffort: selectedReasoningEffort,
              }
            : (payloadBase as AutomationUpdateRequest);
        await onUpdate(editingJob.id, updatePayload);
      } else {
        await onCreate({
          ...(payloadBase as AutomationCreateRequest),
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
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[calc(100vh-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl">
        <DialogHeader className="px-6 pt-6 pb-2 pr-12">
          <DialogTitle>{isEditing ? "Edit automation" : "Add automation"}</DialogTitle>
          <DialogDescription>
            {isEditing
              ? "Update this automation's schedule, model, accounts, and prompt."
              : "Create a scheduled automation. Configure the type, runtime schedule, and prompt to send."}
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
                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Basics</h4>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5 sm:col-span-2">
                  <label htmlFor={FORM_FIELD_IDS.name} className="text-sm font-medium">Name</label>
                  <Input
                    key={`name-${nameInputVersion}`}
                    ref={nameRef}
                    id={FORM_FIELD_IDS.name}
                    placeholder="Automation name"
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
                  <label htmlFor={FORM_FIELD_IDS.type} className="text-sm font-medium">Automation type</label>
                  <Select value={automationType} onValueChange={(value) => setAutomationType(value as AutomationScheduleType)}>
                    <SelectTrigger id={FORM_FIELD_IDS.type}>
                      <SelectValue placeholder="Select automation type" />
                    </SelectTrigger>
                    <SelectContent>
                      {AUTOMATION_TYPE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5">
                  <label htmlFor={FORM_FIELD_IDS.model} className="text-sm font-medium">Model</label>
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
                      <SelectValue placeholder="Select model" />
                    </SelectTrigger>
                    <SelectContent align="start">
                      {modelSelectItems}
                    </SelectContent>
                  </Select>
                  {!modelsLoading && models.length === 0 ? (
                    <p className="text-xs text-destructive">No models available. Add or refresh models first.</p>
                  ) : null}
                  {showFieldError("model") ? (
                    <p id={`${FORM_FIELD_IDS.model}-error`} className="text-xs text-destructive">{showFieldError("model")}</p>
                  ) : null}
                </div>

                <div className="space-y-1.5 sm:col-span-2">
                  <label htmlFor={FORM_FIELD_IDS.reasoning} className="text-sm font-medium">Reasoning effort</label>
                  <Select
                    value={reasoningEffortValue}
                    onValueChange={(value) => {
                      if (reasoningEffortProgrammaticValueRef.current === value) {
                        reasoningEffortProgrammaticValueRef.current = null;
                        return;
                      }
                      reasoningEffortProgrammaticValueRef.current = null;
                      setReasoningEffortValue(value);
                      setReasoningEffortTouched(true);
                    }}
                  >
                    <SelectTrigger id={FORM_FIELD_IDS.reasoning}>
                      <SelectValue placeholder="Model default" />
                    </SelectTrigger>
                    <SelectContent align="start">
                      <SelectItem value={DEFAULT_REASONING_EFFORT_VALUE}>
                        Model default
                        {selectedModelMetadata?.defaultReasoningEffort
                          ? ` (${REASONING_LABELS[selectedModelMetadata.defaultReasoningEffort] ?? selectedModelMetadata.defaultReasoningEffort})`
                          : ""}
                      </SelectItem>
                      {reasoningSelectItems}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    Available values depend on selected model.
                  </p>
                </div>
              </div>
            </section>

            <section className="space-y-3">
              <div className="space-y-0.5">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Schedule</h4>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <label htmlFor={FORM_FIELD_IDS.time} className="text-sm font-medium">Time</label>
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
                      setScheduleTimeValue(parsed.value);
                      setScheduleTimeIsValid(true);
                    }}
                  />
                  <p id={`${FORM_FIELD_IDS.time}-hint`} className="text-xs text-muted-foreground">
                    {timeFormat === "24h" ? "Use 24h format: HH:MM" : "Use 12h format: HH:MM AM/PM"}
                  </p>
                  {showFieldError("time") ? (
                    <p id={`${FORM_FIELD_IDS.time}-error`} className="text-xs text-destructive">{showFieldError("time")}</p>
                  ) : null}
                </div>

                <div className="space-y-1.5">
                  <label htmlFor={FORM_FIELD_IDS.timezone} className="text-sm font-medium">Timezone</label>
                  <Select value={scheduleTimezone} onValueChange={setScheduleTimezone}>
                    <SelectTrigger id={FORM_FIELD_IDS.timezone}>
                      <SelectValue placeholder="Select timezone">
                        {formatTimezoneLabel(scheduleTimezone)}
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {timezoneChoices.map((timezone) => (
                        <SelectItem key={timezone} value={timezone}>
                          {formatTimezoneLabel(timezone)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5 sm:col-span-2">
                  <label htmlFor={FORM_FIELD_IDS.threshold} className="text-sm font-medium">Threshold (minutes)</label>
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
                    0 = immediate dispatch at schedule time. Values above 0 spread per-account runs randomly in that window.
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
                        Include paused accounts
                      </label>
                      <p className="text-xs text-muted-foreground">
                        Include accounts with paused status in execution. Rate-limited, quota-exceeded, and deactivated accounts are always skipped.
                      </p>
                    </div>
                    <Switch
                      id={FORM_FIELD_IDS.includePaused}
                      checked={includePausedAccounts}
                      onCheckedChange={setIncludePausedAccounts}
                      aria-label="Include paused accounts"
                    />
                  </div>
                </div>

                <div className="space-y-1.5 sm:col-span-2">
                  <label htmlFor={FORM_FIELD_IDS.days} className="text-sm font-medium">Days of week</label>
                  <div id={FORM_FIELD_IDS.days} role="group" aria-label="Days of week" className="grid grid-cols-4 gap-1.5 sm:grid-cols-7">
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
                          {option.shortLabel}
                        </Button>
                      );
                    })}
                  </div>
                  <p className="text-xs text-muted-foreground">Choose weekdays when this automation should run.</p>
                </div>
              </div>
            </section>

            <section className="space-y-3">
              <div className="space-y-0.5">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Content / Execution</h4>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5 sm:col-span-2">
                  <label htmlFor={FORM_FIELD_IDS.prompt} className="text-sm font-medium">Prompt</label>
                  <textarea
                    key={`prompt-${promptInputVersion}`}
                    ref={promptRef}
                    id={FORM_FIELD_IDS.prompt}
                    className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive min-h-24 w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px]"
                    placeholder="Type the prompt this automation should send."
                    defaultValue={promptDefaultValue}
                  />
                </div>

                <div className="space-y-1.5 sm:col-span-2">
                  <label htmlFor={FORM_FIELD_IDS.accounts} className="text-sm font-medium">Accounts</label>
                  <AccountMultiSelect
                    value={accountIds}
                    onChange={(value) => {
                      setAccountIds(value);
                      setTouched((current) => ({ ...current, accounts: true }));
                    }}
                    placeholder="All accounts"
                    triggerId={FORM_FIELD_IDS.accounts}
                    ariaInvalid={showFieldError("accounts") ? true : undefined}
                    ariaDescribedBy={showFieldError("accounts") ? `${FORM_FIELD_IDS.accounts}-error` : undefined}
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
                  ? "Resolve highlighted fields to save changes."
                  : "Resolve highlighted fields to create this automation."}
              </p>
            ) : null}
          </div>

          <DialogFooter className="border-t px-6 py-4">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || modelsLoading || !canSubmit}>
              {isEditing ? "Save changes" : "Create automation"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
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
