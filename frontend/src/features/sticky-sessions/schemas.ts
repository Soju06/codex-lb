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

export const StickySessionDeleteResponseSchema = z.object({
  status: z.string().min(1),
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
export type StickySessionsListResponse = z.infer<typeof StickySessionsListResponseSchema>;
export type StickySessionsListParams = z.infer<typeof StickySessionsListParamsSchema>;
export type StickySessionDeleteResponse = z.infer<typeof StickySessionDeleteResponseSchema>;
export type StickySessionsPurgeRequest = z.infer<typeof StickySessionsPurgeRequestSchema>;
export type StickySessionsPurgeResponse = z.infer<typeof StickySessionsPurgeResponseSchema>;
