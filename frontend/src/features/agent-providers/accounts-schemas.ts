import { z } from "zod";

export const AgentProviderAccountSchema = z.object({
  accountId: z.string(),
  providerId: z.enum(["codex", "gemini", "antigravity"]),
  externalAccountId: z.string().nullable().optional(),
  displayName: z.string(),
  status: z.string(),
  authMode: z.string(),
  apiKeySet: z.boolean(),
  credentialFingerprint: z.string().nullable().optional(),
  projectId: z.string().nullable().optional(),
  location: z.string().nullable().optional(),
  createdAt: z.string(),
  updatedAt: z.string(),
});

export const AgentProviderAccountsSchema = z.object({
  accounts: z.array(AgentProviderAccountSchema),
});

export const GeminiProviderAccountCreateSchema = z.object({
  displayName: z.string().min(1),
  apiKey: z.string().min(1),
  externalAccountId: z.string().nullable().optional(),
  projectId: z.string().nullable().optional(),
  location: z.string().nullable().optional(),
});

export const AntigravityProviderAccountCreateSchema = z.object({
  displayName: z.string().min(1),
  authMode: z.enum(["api_key", "cli_keyring"]).optional(),
  apiKey: z.string().min(1).optional(),
  externalAccountId: z.string().nullable().optional(),
  projectId: z.string().nullable().optional(),
  location: z.string().nullable().optional(),
});

export const AgentProviderAccountUpdateSchema = z.object({
  displayName: z.string().min(1).optional(),
  status: z.enum(["active", "paused"]).optional(),
  apiKey: z.string().min(1).optional(),
  externalAccountId: z.string().nullable().optional(),
  projectId: z.string().nullable().optional(),
  location: z.string().nullable().optional(),
});

export type AgentProviderAccount = z.infer<typeof AgentProviderAccountSchema>;
export type AgentProviderAccounts = z.infer<typeof AgentProviderAccountsSchema>;
export type GeminiProviderAccountCreate = z.infer<typeof GeminiProviderAccountCreateSchema>;
export type AntigravityProviderAccountCreate = z.infer<typeof AntigravityProviderAccountCreateSchema>;
export type AgentProviderAccountUpdate = z.infer<typeof AgentProviderAccountUpdateSchema>;
