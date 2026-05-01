import { z } from "zod";

export const PeerFallbackTargetSchema = z.object({
  id: z.string().min(1),
  baseUrl: z.string().url(),
  enabled: z.boolean(),
  createdAt: z.string().datetime({ offset: true }),
  updatedAt: z.string().datetime({ offset: true }),
});

export const PeerFallbackTargetsResponseSchema = z.object({
  targets: z.array(PeerFallbackTargetSchema).default([]),
});

export const PeerFallbackTargetCreateRequestSchema = z.object({
  baseUrl: z.string().url(),
  enabled: z.boolean().optional(),
});

export const PeerFallbackTargetUpdateRequestSchema = z.object({
  baseUrl: z.string().url().optional(),
  enabled: z.boolean().optional(),
});

export const PeerFallbackTargetDeleteResponseSchema = z.object({
  status: z.string().min(1),
});

export type PeerFallbackTarget = z.infer<typeof PeerFallbackTargetSchema>;
export type PeerFallbackTargetsResponse = z.infer<typeof PeerFallbackTargetsResponseSchema>;
export type PeerFallbackTargetCreateRequest = z.infer<typeof PeerFallbackTargetCreateRequestSchema>;
export type PeerFallbackTargetUpdateRequest = z.infer<typeof PeerFallbackTargetUpdateRequestSchema>;
export type PeerFallbackTargetDeleteResponse = z.infer<typeof PeerFallbackTargetDeleteResponseSchema>;
