import { z } from "zod";

export const AuthSessionSchema = z.object({
  authenticated: z.boolean(),
  passwordRequired: z.boolean(),
  totpRequiredOnLogin: z.boolean(),
  totpConfigured: z.boolean(),
});

export const LoginRequestSchema = z.object({
  password: z.string().min(1),
});

export const PasswordSetupRequestSchema = z.object({
  password: z.string().min(8),
});

export const PasswordChangeRequestSchema = z.object({
  currentPassword: z.string().min(1),
  newPassword: z.string().min(8),
});

export const PasswordRemoveRequestSchema = z.object({
  password: z.string().min(1),
});

export const TotpVerifyRequestSchema = z.object({
  code: z.string().min(6).max(6),
});

export const TotpSetupConfirmRequestSchema = z.object({
  secret: z.string().min(1),
  code: z.string().min(6).max(6),
});

export const TotpSetupStartResponseSchema = z.object({
  secret: z.string(),
  otpauthUri: z.string(),
  qrSvgDataUri: z.string(),
});

export const StatusResponseSchema = z.object({
  status: z.string(),
});

export type AuthSession = z.infer<typeof AuthSessionSchema>;
export type LoginRequest = z.infer<typeof LoginRequestSchema>;
export type PasswordSetupRequest = z.infer<typeof PasswordSetupRequestSchema>;
export type PasswordChangeRequest = z.infer<typeof PasswordChangeRequestSchema>;
export type PasswordRemoveRequest = z.infer<typeof PasswordRemoveRequestSchema>;
export type TotpVerifyRequest = z.infer<typeof TotpVerifyRequestSchema>;
export type TotpSetupConfirmRequest = z.infer<typeof TotpSetupConfirmRequestSchema>;
export type TotpSetupStartResponse = z.infer<typeof TotpSetupStartResponseSchema>;
export type StatusResponse = z.infer<typeof StatusResponseSchema>;
