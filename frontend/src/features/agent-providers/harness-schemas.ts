import { z } from "zod";

export const AntigravityHarnessPrintRequestSchema = z.object({
  prompt: z.string().min(1),
  workspacePath: z.string().min(1),
  timeoutSeconds: z.number().min(1).max(1800).optional(),
  addDirs: z.array(z.string().min(1)).optional(),
  conversationId: z.string().nullable().optional(),
  continueConversation: z.boolean().optional(),
  sandbox: z.string().nullable().optional(),
});

export const AntigravityHarnessPrintResponseSchema = z.object({
  providerId: z.literal("antigravity"),
  accountId: z.string(),
  externalAccountId: z.string().nullable().optional(),
  command: z.array(z.string()),
  cwd: z.string(),
  exitCode: z.number(),
  stdout: z.string(),
  stderr: z.string(),
  durationMs: z.number(),
});

export const AntigravityManagedInteractionRunRequestSchema = z.object({
  agent: z.string().min(1).optional(),
  input: z.string().min(1),
  environment: z.string().min(1).optional(),
  tools: z.array(z.record(z.string(), z.unknown())).optional(),
});

export const AntigravityManagedInteractionRunResponseSchema = z.object({
  providerId: z.literal("antigravity"),
  agent: z.string(),
  outputText: z.string(),
  response: z.record(z.string(), z.unknown()),
});

export type AntigravityHarnessPrintRequest = z.infer<typeof AntigravityHarnessPrintRequestSchema>;
export type AntigravityHarnessPrintResponse = z.infer<typeof AntigravityHarnessPrintResponseSchema>;
export type AntigravityManagedInteractionRunRequest = z.infer<
  typeof AntigravityManagedInteractionRunRequestSchema
>;
export type AntigravityManagedInteractionRunResponse = z.infer<
  typeof AntigravityManagedInteractionRunResponseSchema
>;
