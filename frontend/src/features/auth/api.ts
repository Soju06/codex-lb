import { del, get, post } from "@/lib/api-client";
import {
  AuthSessionSchema,
  GuestPasswordSetRequestSchema,
  type GuestLoginRequest,
  type InviteAcceptRequest,
  InviteDescriptionSchema,
  type GuestPasswordSetRequest,
  type LoginRequest,
  type PasswordChangeRequest,
  type PasswordRemoveRequest,
  type PasswordSetupRequest,
  StatusResponseSchema,
  type StepUpRequest,
  StepUpResponseSchema,
  TotpSetupConfirmRequestSchema,
  TotpSetupStartResponseSchema,
  TotpVerifyRequestSchema,
} from "@/features/auth/schemas";

const AUTH_BASE_PATH = "/api/dashboard-auth";

export function getAuthSession() {
  return get(`${AUTH_BASE_PATH}/session`, AuthSessionSchema);
}

export function setupPassword(payload: PasswordSetupRequest) {
  return post(`${AUTH_BASE_PATH}/password/setup`, AuthSessionSchema, {
    body: payload,
  });
}

export function loginPassword(payload: LoginRequest) {
  return post(`${AUTH_BASE_PATH}/password/login`, AuthSessionSchema, {
    body: payload,
    suppressUnauthorizedHandler: true,
  });
}

export function loginGuest(payload: GuestLoginRequest = {}) {
  return post(`${AUTH_BASE_PATH}/guest/login`, AuthSessionSchema, {
    body: payload,
    suppressUnauthorizedHandler: true,
  });
}

export function setGuestPassword(payload: GuestPasswordSetRequest) {
  const validated = GuestPasswordSetRequestSchema.parse(payload);
  return post(`${AUTH_BASE_PATH}/guest/password`, StatusResponseSchema, {
    body: validated,
  });
}

export function removeGuestPassword() {
  return del(`${AUTH_BASE_PATH}/guest/password`, StatusResponseSchema);
}

export function changePassword(payload: PasswordChangeRequest) {
  return post(`${AUTH_BASE_PATH}/password/change`, StatusResponseSchema, {
    body: payload,
  });
}

export function removePassword(payload: PasswordRemoveRequest) {
  return del(`${AUTH_BASE_PATH}/password`, StatusResponseSchema, {
    body: payload,
  });
}

export function startTotpSetup() {
  return post(`${AUTH_BASE_PATH}/totp/setup/start`, TotpSetupStartResponseSchema);
}

export function confirmTotpSetup(payload: unknown) {
  const validated = TotpSetupConfirmRequestSchema.parse(payload);
  return post(`${AUTH_BASE_PATH}/totp/setup/confirm`, StatusResponseSchema, {
    body: validated,
  });
}

export function verifyTotp(payload: unknown) {
  const validated = TotpVerifyRequestSchema.parse(payload);
  return post(`${AUTH_BASE_PATH}/totp/verify`, AuthSessionSchema, {
    body: validated,
  });
}

export function disableTotp(payload: unknown) {
  const validated = TotpVerifyRequestSchema.parse(payload);
  return post(`${AUTH_BASE_PATH}/totp/disable`, StatusResponseSchema, {
    body: validated,
  });
}

/** Re-verify the signed-in account for a sensitive change; the server picks the factors from the account. */
export function stepUp(payload: StepUpRequest) {
  return post(`${AUTH_BASE_PATH}/step-up`, StepUpResponseSchema, {
    body: payload,
    suppressUnauthorizedHandler: true,
  });
}

export function logout() {
  return post(`${AUTH_BASE_PATH}/logout`, StatusResponseSchema);
}

/** Revokes every session of the signed-in account, including this one. */
export function logoutAll() {
  return post(`${AUTH_BASE_PATH}/logout-all`, StatusResponseSchema);
}

/** Public: what the acceptance screen may show for an invite token (404 for every invalid token). */
export function describeInvite(token: string) {
  return get(`${AUTH_BASE_PATH}/invite/${encodeURIComponent(token)}`, InviteDescriptionSchema, {
    suppressUnauthorizedHandler: true,
  });
}

/** Public: sets the invited account's password and signs it in (session cookie). */
export function acceptInvite(payload: InviteAcceptRequest) {
  return post(`${AUTH_BASE_PATH}/invite/accept`, AuthSessionSchema, {
    body: payload,
    suppressUnauthorizedHandler: true,
  });
}
