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

// Fine-grained permissions (`<resource>:<action>`); mirrors `Permission` in
// `app/core/auth/dashboard_access.py`. The session lists each grant as
// `<permission>:<scope>` after the coarse aliases.
export const PermissionSchema = z.enum([
  "dashboard:read",
  "accounts:read",
  "accounts:write",
  "accounts:export",
  "api_keys:read",
  "api_keys:write",
  "api_keys:assign",
  "ops:write",
  "security:write",
  "users:manage",
  "roles:manage",
  "conversations:read",
  "audit:read",
]);
export const PermissionScopeSchema = z.enum(["all", "own"]);

export const AuthSessionUserSchema = z.object({
  id: z.string(),
  username: z.string(),
  displayName: z.string().nullable().default(null),
  role: z.object({
    id: z.string(),
    slug: z.string(),
    name: z.string(),
    kind: z.string(),
  }),
});

export const LoginProviderSchema = z.object({
  kind: z.string(),
  providerKey: z.string().default("default"),
  label: z.string(),
  loginUrl: z.string().nullable().default(null),
});

// Who may still sign in with a local password (`dashboard_settings.local_login_policy`).
// The strict enum is what a *request* must satisfy: `updateSettings` takes
// `unknown`, so this schema is the only thing standing between a bad value and
// the wire, and a fallback there would silently send the most open policy of
// the three.
export const StrictLocalLoginPolicySchema = z.enum(["enabled", "admins_only", "break_glass_only"]);
// Responses get the fallback instead: a value this build does not know must not
// fail the whole session parse. Nothing may echo the fallen-back value into an
// update — see `buildSettingsUpdateRequest`.
export const LocalLoginPolicySchema = StrictLocalLoginPolicySchema.catch("enabled");

export const LoginHintSchema = z.object({
  usernameField: z.enum(["hidden", "shown"]).default("hidden"),
  providers: z
    .array(LoginProviderSchema)
    .default([{ kind: "password", providerKey: "default", label: "Password", loginUrl: null }]),
  localLogin: LocalLoginPolicySchema.default("enabled"),
  // The request carried a provider identity that has no account here yet.
  pendingIdentity: z.boolean().default(false),
});

// Team-size facts served only to `users:manage` holders; `null` for everyone
// else. Consumed exclusively by `resolveDisclosureTier`.
export const AccessSummarySchema = z.object({
  usersTotal: z.number().int().default(0),
  usersActive: z.number().int().default(0),
  usersInvited: z.number().int().default(0),
  usersDisabled: z.number().int().default(0),
  pendingInvites: z.number().int().default(0),
  nonAdminUsers: z.number().int().default(0),
  customRoles: z.number().int().default(0),
  providersEnabled: z.array(z.string()).default([]),
  roleMappings: z.number().int().default(0),
  scimTokens: z.number().int().default(0),
  auditSinks: z.number().int().default(0),
  localLoginPolicy: LocalLoginPolicySchema.default("enabled"),
});

// Step-up (re-verification for sensitive changes): when the account last
// re-verified, and which factors `/step-up` will ask for. Empty `methods`
// means the account must enrol two-factor or set a password first.
export const StepUpMethodSchema = z.enum(["password", "totp"]);
export const StepUpStateSchema = z.object({
  verifiedAt: z.number().int().nullable().default(null),
  expiresAt: z.number().int().nullable().default(null),
  methods: z.array(StepUpMethodSchema).default([]),
});

export const AuthSessionSchema = z.object({
  authenticated: z.boolean(),
  passwordRequired: z.boolean(),
  // An active account holds a password (proxy-created accounts do not count).
  localPasswordConfigured: z.boolean().default(false),
  totpRequiredOnLogin: z.boolean(),
  totpConfigured: z.boolean(),
  bootstrapRequired: z.boolean().optional().default(false),
  bootstrapTokenConfigured: z.boolean().optional().default(false),
  authMode: DashboardAuthModeSchema.default("standard"),
  passwordManagementEnabled: z.boolean().default(true),
  passwordSessionActive: z.boolean().default(false),
  // Least-privilege defaults: a response that omits these fields must never
  // be treated as an admin. `permissions` accepts any string so that future
  // fine-grained values (e.g. `accounts:export`) do not reject the session.
  role: DashboardRoleSchema.default("guest"),
  permissions: z.array(z.string()).default([]),
  guestAccessEnabled: z.boolean().default(false),
  guestPasswordRequired: z.boolean().default(false),
  // Account fields (user-login-and-session-v2). Every default is the
  // "no account / individual install" shape so older payloads parse unchanged.
  user: AuthSessionUserSchema.nullable().default(null),
  authMethod: z.string().nullable().default(null),
  mustChangePassword: z.boolean().default(false),
  totpEnrollmentRequired: z.boolean().default(false),
  login: LoginHintSchema.nullable()
    .default(null)
    .transform((value) => value ?? LoginHintSchema.parse({})),
  accessSummary: AccessSummarySchema.nullable().default(null),
  assignableRoleIds: z.array(z.string()).default([]),
  stepUp: StepUpStateSchema.nullable().default(null),
  // The session was minted for an account carrying the break-glass
  // designation; the header says so, nothing else changes.
  breakGlassSession: z.boolean().default(false),
});

// Mirrors the backend username rule (case-folded on the server).
const USERNAME_PATTERN = /^[a-z0-9._-]{1,64}$/i;
const usernameSchema = z
  .string()
  .trim()
  .min(1, { message: "auth.invite.validation.usernameRequired" })
  .max(64, { message: "auth.invite.validation.usernameTooLong" })
  .regex(USERNAME_PATTERN, { message: "auth.invite.validation.usernameFormat" });

export const LoginRequestSchema = z.object({
  username: z.string().trim().max(64, { message: "auth.invite.validation.usernameTooLong" }).optional(),
  password: z.string().min(1),
});

export const InviteDescriptionSchema = z.object({
  roleName: z.string(),
  inviterDisplayName: z.string().nullable().default(null),
  suggestedUsername: z.string(),
  usernameLocked: z.boolean(),
  expiresAt: z.string(),
});

export const InviteAcceptRequestSchema = z.object({
  token: z.string().min(1),
  username: usernameSchema,
  password: dashboardPasswordSchema,
  displayName: z.string().trim().max(128).optional(),
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

export const StepUpRequestSchema = z.object({
  password: z.string().min(1).optional(),
  code: z.string().length(6, "settings.totp.validation.codeLength").optional(),
});

export const StepUpResponseSchema = z.object({
  verifiedAt: z.number().int(),
  expiresAt: z.number().int(),
});

export type AuthSession = z.infer<typeof AuthSessionSchema>;
export type AuthSessionUser = z.infer<typeof AuthSessionUserSchema>;
export type LoginHint = z.infer<typeof LoginHintSchema>;
export type LoginProvider = z.infer<typeof LoginProviderSchema>;
export type LocalLoginPolicy = z.infer<typeof LocalLoginPolicySchema>;
export type AccessSummary = z.infer<typeof AccessSummarySchema>;
export type Permission = z.infer<typeof PermissionSchema>;
export type PermissionScope = z.infer<typeof PermissionScopeSchema>;
export type InviteDescription = z.infer<typeof InviteDescriptionSchema>;
export type InviteAcceptRequest = z.infer<typeof InviteAcceptRequestSchema>;
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
export type StepUpState = z.infer<typeof StepUpStateSchema>;
export type StepUpRequest = z.infer<typeof StepUpRequestSchema>;

export function getFirstZodIssueMessage(error: unknown): string | null {
  if (!(error instanceof z.ZodError)) {
    return null;
  }

  const [firstIssue] = error.issues;
  return typeof firstIssue?.message === "string" ? firstIssue.message : null;
}
