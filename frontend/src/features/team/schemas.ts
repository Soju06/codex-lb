import { z } from "zod";

import { ApiKeyCreateResponseSchema } from "@/features/api-keys/schemas";

export const TEAM_STATUSES = ["active", "suspended"] as const;
export type TeamMemberStatus = (typeof TEAM_STATUSES)[number];

export const TEAM_GATES = ["ok", "near_cap", "over_cap", "suspended"] as const;
export type TeamGate = (typeof TEAM_GATES)[number];

export const TEAM_WINDOWS = ["day", "week", "month"] as const;
export type TeamWindow = (typeof TEAM_WINDOWS)[number];

export const TeamUsageWindowSchema = z.object({
  costUsd: z.number().default(0),
  tokens: z.number().int().default(0),
});

export const TeamUsageSchema = z.object({
  day: TeamUsageWindowSchema,
  week: TeamUsageWindowSchema,
  month: TeamUsageWindowSchema,
});

export const TeamMemberKeySchema = z.object({
  id: z.string(),
  name: z.string(),
  keyPrefix: z.string(),
  isActive: z.boolean(),
  lastUsedAt: z.string().nullable().default(null),
});

export const TeamMemberSchema = z.object({
  id: z.string(),
  name: z.string(),
  email: z.string().nullable().default(null),
  status: z.enum(TEAM_STATUSES),
  costCapDayUsd: z.number().nullable().default(null),
  costCapWeekUsd: z.number().nullable().default(null),
  costCapMonthUsd: z.number().nullable().default(null),
  tokenCapDay: z.number().nullable().default(null),
  tokenCapWeek: z.number().nullable().default(null),
  tokenCapMonth: z.number().nullable().default(null),
  allowedModels: z.array(z.string()).nullable().default(null),
  notes: z.string().nullable().default(null),
  createdAt: z.string(),
  updatedAt: z.string(),
  usage: TeamUsageSchema,
  gate: z.enum(TEAM_GATES),
  keys: z.array(TeamMemberKeySchema).default([]),
});

export const TeamMemberListSchema = z.array(TeamMemberSchema);

const capsShape = {
  costCapDayUsd: z.number().positive().nullable().optional(),
  costCapWeekUsd: z.number().positive().nullable().optional(),
  costCapMonthUsd: z.number().positive().nullable().optional(),
  tokenCapDay: z.number().int().positive().nullable().optional(),
  tokenCapWeek: z.number().int().positive().nullable().optional(),
  tokenCapMonth: z.number().int().positive().nullable().optional(),
};

export const TeamMemberCreateRequestSchema = z.object({
  name: z.string().min(1).max(128),
  email: z.string().max(320).nullable().optional(),
  status: z.enum(TEAM_STATUSES).optional(),
  allowedModels: z.array(z.string()).nullable().optional(),
  notes: z.string().max(4000).nullable().optional(),
  ...capsShape,
});

export const TeamMemberUpdateRequestSchema = z.object({
  name: z.string().min(1).max(128).optional(),
  email: z.string().max(320).nullable().optional(),
  status: z.enum(TEAM_STATUSES).optional(),
  allowedModels: z.array(z.string()).nullable().optional(),
  notes: z.string().max(4000).nullable().optional(),
  ...capsShape,
});

export const TeamMemberKeyCreateRequestSchema = z.object({
  name: z.string().min(1).max(128).optional(),
  expiresAt: z.string().nullable().optional(),
});

export const TeamUsageModelSchema = z.object({
  model: z.string(),
  costUsd: z.number().default(0),
  tokens: z.number().int().default(0),
  requests: z.number().int().default(0),
});

export const TeamUsageDaySchema = z.object({
  day: z.string(),
  costUsd: z.number().default(0),
  tokens: z.number().int().default(0),
});

export const TeamMemberUsageSchema = z.object({
  memberId: z.string(),
  window: z.enum(TEAM_WINDOWS),
  windowStart: z.string(),
  windowEnd: z.string(),
  totals: TeamUsageWindowSchema,
  models: z.array(TeamUsageModelSchema).default([]),
  series: z.array(TeamUsageDaySchema).default([]),
});

export const TeamOnboardingSchema = z.object({
  baseUrl: z.string(),
  snippets: z.object({
    windowsPowershell: z.string(),
    macosZsh: z.string(),
  }),
});

export const TeamMemberKeyCreateResponseSchema = ApiKeyCreateResponseSchema;

export type TeamMember = z.infer<typeof TeamMemberSchema>;
export type TeamMemberKey = z.infer<typeof TeamMemberKeySchema>;
export type TeamUsageWindow = z.infer<typeof TeamUsageWindowSchema>;
export type TeamMemberCreateRequest = z.infer<typeof TeamMemberCreateRequestSchema>;
export type TeamMemberUpdateRequest = z.infer<typeof TeamMemberUpdateRequestSchema>;
export type TeamMemberKeyCreateRequest = z.infer<typeof TeamMemberKeyCreateRequestSchema>;
export type TeamMemberUsage = z.infer<typeof TeamMemberUsageSchema>;
export type TeamOnboarding = z.infer<typeof TeamOnboardingSchema>;
