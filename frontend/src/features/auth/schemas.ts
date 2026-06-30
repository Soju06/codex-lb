import { z } from "zod";

// Mirrors backend `_MAX_PASSWORD_BYTES` in `app/modules/dashboard_auth/api.py`.
// bcrypt only hashes the first 72 bytes of input; the backend rejects
// anything longer with HTTP 422 `password_too_long`. Validate the same
// budget on the client so users see the failure inline instead of after a
// round-trip. Multi-byte characters (e.g. emoji) consume their UTF-8 byte
// count, not the visible character count.
export const MAX_DASHBOARD_PASSWORD_BYTES = 72;

const dashboardPasswordByteLength = (value: string): number => new TextEncoder().encode(value).length;

const PASSWORD_REQUIRED_MESSAGE = "settings.password.validation.required";

const dashboardPasswordSchema = z
  .string()
  .min(8, {
    message: "settings.password.validation.minLength",
  })
  .refine((value) => dashboardPasswordByteLength(value) <= MAX_DASHBOARD_PASSWORD_BYTES, {
    message: "settings.password.validation.maxByteLength",
  });

export const DashboardAuthModeSchema = z.enum(["standard", "trusted_header", "disabled"]);
export const DashboardRoleSchema = z.enum(["admin", "guest"]);
export const DashboardPermissionSchema = z.enum(["read", "write"]);

export const AuthSessionSchema = z.object({
  authenticated: z.boolean(),
  passwordRequired: z.boolean(),
  totpRequiredOnLogin: z.boolean(),
  totpConfigured: z.boolean(),
  bootstrapRequired: z.boolean().optional().default(false),
  bootstrapTokenConfigured: z.boolean().optional().default(false),
  authMode: DashboardAuthModeSchema.default("standard"),
  passwordManagementEnabled: z.boolean().default(true),
  passwordSessionActive: z.boolean().default(false),
  role: DashboardRoleSchema.default("admin"),
  permissions: z.array(DashboardPermissionSchema).default(["read", "write"]),
  guestAccessEnabled: z.boolean().default(false),
  guestPasswordRequired: z.boolean().default(false),
});

export const LoginRequestSchema = z.object({
  password: z.string().min(1),
});

export const GuestLoginRequestSchema = z.object({
  password: z.string().optional(),
});

export const PasswordSetupRequestSchema = z.object({
  password: dashboardPasswordSchema,
  bootstrapToken: z.string().optional(),
});

export const GuestPasswordSetRequestSchema = z.object({
  password: dashboardPasswordSchema,
});

export const PasswordChangeRequestSchema = z.object({
  currentPassword: z.string().min(1, { message: PASSWORD_REQUIRED_MESSAGE }),
  newPassword: dashboardPasswordSchema,
});

export const PasswordRemoveRequestSchema = z.object({
  password: z.string().min(1, { message: PASSWORD_REQUIRED_MESSAGE }),
});

export const TotpVerifyRequestSchema = z.object({
  code: z.string().length(6, "settings.totp.validation.codeLength"),
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
export type DashboardAuthMode = z.infer<typeof DashboardAuthModeSchema>;
export type DashboardRole = z.infer<typeof DashboardRoleSchema>;
export type DashboardPermission = z.infer<typeof DashboardPermissionSchema>;
export type LoginRequest = z.infer<typeof LoginRequestSchema>;
export type GuestLoginRequest = z.infer<typeof GuestLoginRequestSchema>;
export type PasswordSetupRequest = z.infer<typeof PasswordSetupRequestSchema>;
export type GuestPasswordSetRequest = z.infer<typeof GuestPasswordSetRequestSchema>;
export type PasswordChangeRequest = z.infer<typeof PasswordChangeRequestSchema>;
export type PasswordRemoveRequest = z.infer<typeof PasswordRemoveRequestSchema>;
export type TotpVerifyRequest = z.infer<typeof TotpVerifyRequestSchema>;
export type TotpSetupConfirmRequest = z.infer<typeof TotpSetupConfirmRequestSchema>;
export type TotpSetupStartResponse = z.infer<typeof TotpSetupStartResponseSchema>;
export type StatusResponse = z.infer<typeof StatusResponseSchema>;

export function getFirstZodIssueMessage(error: unknown): string | null {
  if (!(error instanceof z.ZodError)) {
    return null;
  }

  const [firstIssue] = error.issues;
  return typeof firstIssue?.message === "string" ? firstIssue.message : null;
}
