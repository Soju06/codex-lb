import { z } from "zod";

export const AutomationScheduleTypeSchema = z.enum(["daily"]);
export const AutomationScheduleDaySchema = z.enum(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]);
export const AutomationRunStatusSchema = z.enum(["running", "success", "failed", "partial"]);
export const AutomationRunTriggerSchema = z.enum(["scheduled", "manual"]);
export const AutomationReasoningEffortSchema = z.enum(["minimal", "low", "medium", "high", "xhigh"]);

export const AutomationScheduleDaysSchema = z
  .array(AutomationScheduleDaySchema)
  .min(1)
  .max(7)
  .refine((value) => new Set(value).size === value.length, "Duplicate schedule days are not allowed");

export const AutomationScheduleSchema = z.object({
  type: AutomationScheduleTypeSchema,
  time: z.string().regex(/^\d{2}:\d{2}$/),
  timezone: z.string().min(1),
  thresholdMinutes: z.number().int().min(0).max(240).default(0),
  days: AutomationScheduleDaysSchema,
});

export const AutomationRunSchema = z.object({
  id: z.string().min(1),
  jobId: z.string().min(1),
  jobName: z.string().nullable().optional(),
  model: z.string().nullable().optional(),
  reasoningEffort: AutomationReasoningEffortSchema.nullable().optional(),
  trigger: AutomationRunTriggerSchema,
  status: AutomationRunStatusSchema,
  scheduledFor: z.string().datetime({ offset: true }),
  startedAt: z.string().datetime({ offset: true }),
  finishedAt: z.string().datetime({ offset: true }).nullable(),
  accountId: z.string().nullable(),
  errorCode: z.string().nullable(),
  errorMessage: z.string().nullable(),
  attemptCount: z.number().int().nonnegative(),
  effectiveStatus: AutomationRunStatusSchema.nullable().optional(),
  totalAccounts: z.number().int().nonnegative().nullable().optional(),
  completedAccounts: z.number().int().nonnegative().nullable().optional(),
  pendingAccounts: z.number().int().nonnegative().nullable().optional(),
  cycleKey: z.string().nullable().optional(),
});

export const AutomationJobSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  enabled: z.boolean(),
  includePausedAccounts: z.boolean().default(false),
  schedule: AutomationScheduleSchema,
  model: z.string().min(1),
  reasoningEffort: AutomationReasoningEffortSchema.nullable().optional(),
  prompt: z.string().min(1),
  accountIds: z.array(z.string().min(1)).default([]),
  nextRunAt: z.string().datetime({ offset: true }).nullable(),
  lastRun: AutomationRunSchema.nullable(),
});

export const AutomationsListResponseSchema = z.object({
  items: z.array(AutomationJobSchema).default([]),
  total: z.number().int().nonnegative().default(0),
  hasMore: z.boolean().default(false),
});

export const AutomationRunsListResponseSchema = z.object({
  items: z.array(AutomationRunSchema).default([]),
  total: z.number().int().nonnegative().default(0),
  hasMore: z.boolean().default(false),
});

export const AutomationJobFilterOptionsSchema = z.object({
  accountIds: z.array(z.string().min(1)).default([]),
  models: z.array(z.string().min(1)).default([]),
  statuses: z.array(z.string().min(1)).default([]),
  scheduleTypes: z.array(z.string().min(1)).default([]),
});

export const AutomationRunFilterOptionsSchema = z.object({
  accountIds: z.array(z.string().min(1)).default([]),
  models: z.array(z.string().min(1)).default([]),
  statuses: z.array(z.string().min(1)).default([]),
  triggers: z.array(z.string().min(1)).default([]),
});

export const AutomationRunAccountStateSchema = z.object({
  accountId: z.string().min(1),
  status: z.enum(["pending", "running", "success", "failed", "partial"]),
  runId: z.string().nullable().optional(),
  scheduledFor: z.string().datetime({ offset: true }).nullable().optional(),
  startedAt: z.string().datetime({ offset: true }).nullable().optional(),
  finishedAt: z.string().datetime({ offset: true }).nullable().optional(),
  errorCode: z.string().nullable().optional(),
  errorMessage: z.string().nullable().optional(),
});

export const AutomationRunDetailsSchema = z.object({
  run: AutomationRunSchema,
  accounts: z.array(AutomationRunAccountStateSchema).default([]),
  totalAccounts: z.number().int().nonnegative(),
  completedAccounts: z.number().int().nonnegative(),
  pendingAccounts: z.number().int().nonnegative(),
});

export const AutomationCreateRequestSchema = z.object({
  name: z.string().min(1),
  enabled: z.boolean().default(true),
  includePausedAccounts: z.boolean().optional(),
  schedule: AutomationScheduleSchema,
  model: z.string().min(1),
  reasoningEffort: AutomationReasoningEffortSchema.nullable().optional(),
  prompt: z.string().optional(),
  accountIds: z.array(z.string().min(1)),
});

export const AutomationUpdateRequestSchema = z.object({
  name: z.string().min(1).optional(),
  enabled: z.boolean().optional(),
  includePausedAccounts: z.boolean().optional(),
  schedule: AutomationScheduleSchema.optional(),
  model: z.string().min(1).optional(),
  reasoningEffort: AutomationReasoningEffortSchema.nullable().optional(),
  prompt: z.string().min(1).optional(),
  accountIds: z.array(z.string().min(1)).optional(),
});

export const AutomationDeleteResponseSchema = z.object({
  status: z.literal("deleted"),
});

export type AutomationScheduleType = z.infer<typeof AutomationScheduleTypeSchema>;
export type AutomationScheduleDay = z.infer<typeof AutomationScheduleDaySchema>;
export type AutomationRunStatus = z.infer<typeof AutomationRunStatusSchema>;
export type AutomationRunTrigger = z.infer<typeof AutomationRunTriggerSchema>;
export type AutomationReasoningEffort = z.infer<typeof AutomationReasoningEffortSchema>;
export type AutomationSchedule = z.infer<typeof AutomationScheduleSchema>;
export type AutomationRun = z.infer<typeof AutomationRunSchema>;
export type AutomationJob = z.infer<typeof AutomationJobSchema>;
export type AutomationsListResponse = z.infer<typeof AutomationsListResponseSchema>;
export type AutomationRunsListResponse = z.infer<typeof AutomationRunsListResponseSchema>;
export type AutomationCreateRequest = z.infer<typeof AutomationCreateRequestSchema>;
export type AutomationUpdateRequest = z.infer<typeof AutomationUpdateRequestSchema>;
export type AutomationDeleteResponse = z.infer<typeof AutomationDeleteResponseSchema>;
export type AutomationJobFilterOptions = z.infer<typeof AutomationJobFilterOptionsSchema>;
export type AutomationRunFilterOptions = z.infer<typeof AutomationRunFilterOptionsSchema>;
export type AutomationRunAccountState = z.infer<typeof AutomationRunAccountStateSchema>;
export type AutomationRunDetails = z.infer<typeof AutomationRunDetailsSchema>;
