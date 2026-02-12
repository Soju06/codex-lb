import { del, get, post } from "@/lib/api-client";
import {
  AuthSessionSchema,
  type LoginRequest,
  type PasswordChangeRequest,
  type PasswordRemoveRequest,
  type PasswordSetupRequest,
  StatusResponseSchema,
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
  });
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

export function logout() {
  return post(`${AUTH_BASE_PATH}/logout`, StatusResponseSchema);
}
