import { z } from "zod";

export const FIREWALL_MODES = ["allow_all", "allowlist_active"] as const;

export const FirewallModeSchema = z.enum(FIREWALL_MODES);

export const FirewallIpEntrySchema = z.object({
  ipAddress: z.string().min(1),
  createdAt: z.string().datetime({ offset: true }),
});

export const FirewallIpsResponseSchema = z.object({
  mode: FirewallModeSchema,
  entries: z.array(FirewallIpEntrySchema).default([]),
});

export const FirewallIpCreateRequestSchema = z.object({
  ipAddress: z.string().min(1),
});

export const FirewallDeleteResponseSchema = z.object({
  status: z.string().min(1),
});

export type FirewallMode = z.infer<typeof FirewallModeSchema>;
export type FirewallIpEntry = z.infer<typeof FirewallIpEntrySchema>;
export type FirewallIpsResponse = z.infer<typeof FirewallIpsResponseSchema>;
export type FirewallIpCreateRequest = z.infer<typeof FirewallIpCreateRequestSchema>;
export type FirewallDeleteResponse = z.infer<typeof FirewallDeleteResponseSchema>;
