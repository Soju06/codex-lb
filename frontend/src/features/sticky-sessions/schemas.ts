import { z } from "zod";

export const STICKY_SESSION_KINDS = ["codex_session", "sticky_thread", "prompt_cache"] as const;

export const StickySessionKindSchema = z.enum(STICKY_SESSION_KINDS);

export const StickySessionEntrySchema = z.object({
  key: z.string().min(1),
  displayName: z.string().min(1),
  kind: StickySessionKindSchema,
  createdAt: z.string().datetime({ offset: true }),
  updatedAt: z.string().datetime({ offset: true }),
  expiresAt: z.string().datetime({ offset: true }).nullable(),
  isStale: z.boolean(),
});

export const StickySessionIdentifierSchema = z.object({
  key: z.string().min(1),
  kind: StickySessionKindSchema,
});

export const StickySessionsDeleteRequestSchema = z.object({
  sessions: z
    .array(StickySessionIdentifierSchema)
    .min(1)
    .max(500)
    .refine(
      (sessions) => new Set(sessions.map((session) => `${session.kind}:${session.key}`)).size === sessions.length,
      "Duplicate sticky session targets are not allowed",
    ),
});

export const StickySessionDeleteFailureSchema = z.object({
  key: z.string().min(1),
  kind: StickySessionKindSchema,
  reason: z.string().min(1),
});

export const StickySessionsListResponseSchema = z.object({
  entries: z.array(StickySessionEntrySchema).default([]),
  stalePromptCacheCount: z.number().int().nonnegative().default(0),
  total: z.number().int().nonnegative().default(0),
  hasMore: z.boolean().default(false),
});

export const StickySessionsListParamsSchema = z.object({
  staleOnly: z.boolean().default(false),
  offset: z.number().int().nonnegative().default(0),
  limit: z.number().int().positive().max(500).default(10),
});

export const StickySessionsDeleteResponseSchema = z.object({
  deletedCount: z.number().int().nonnegative(),
  deleted: z.array(StickySessionIdentifierSchema).default([]),
  failed: z.array(StickySessionDeleteFailureSchema).default([]),
});

export const StickySessionsPurgeRequestSchema = z.object({
  staleOnly: z.boolean().default(true),
});

export const StickySessionsPurgeResponseSchema = z.object({
  deletedCount: z.number().int().nonnegative(),
});

export type StickySessionKind = z.infer<typeof StickySessionKindSchema>;
export type StickySessionEntry = z.infer<typeof StickySessionEntrySchema>;
export type StickySessionIdentifier = z.infer<typeof StickySessionIdentifierSchema>;
export type StickySessionsDeleteRequest = z.infer<typeof StickySessionsDeleteRequestSchema>;
export type StickySessionsListResponse = z.infer<typeof StickySessionsListResponseSchema>;
export type StickySessionsListParams = z.infer<typeof StickySessionsListParamsSchema>;
export type StickySessionDeleteFailure = z.infer<typeof StickySessionDeleteFailureSchema>;
export type StickySessionsDeleteResponse = z.infer<typeof StickySessionsDeleteResponseSchema>;
export type StickySessionsPurgeRequest = z.infer<typeof StickySessionsPurgeRequestSchema>;
export type StickySessionsPurgeResponse = z.infer<typeof StickySessionsPurgeResponseSchema>;
