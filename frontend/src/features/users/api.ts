import { del, get, patch, post } from "@/lib/api-client";

import {
  DashboardUserCreateRequestSchema,
  DashboardUserDeleteResponseSchema,
  DashboardUsersResponseSchema,
  DashboardUserSchema,
  DashboardUserUpdateRequestSchema,
  type DashboardUserRole,
} from "@/features/users/schemas";

const USERS_BASE_PATH = "/api/users";

export type ListUsersParams = {
  search?: string;
  role?: DashboardUserRole;
};

export function listUsers(params: ListUsersParams = {}) {
  const query = new URLSearchParams();
  if (params.search) {
    query.set("search", params.search);
  }
  if (params.role) {
    query.set("role", params.role);
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return get(`${USERS_BASE_PATH}${suffix}`, DashboardUsersResponseSchema);
}

export function createUser(payload: unknown) {
  const validated = DashboardUserCreateRequestSchema.parse(payload);
  return post(USERS_BASE_PATH, DashboardUserSchema, {
    body: validated,
  });
}

export function updateUser(userId: string, payload: unknown) {
  const validated = DashboardUserUpdateRequestSchema.parse(payload);
  return patch(`${USERS_BASE_PATH}/${encodeURIComponent(userId)}`, DashboardUserSchema, {
    body: validated,
  });
}

export function deleteUser(userId: string) {
  return del(`${USERS_BASE_PATH}/${encodeURIComponent(userId)}`, DashboardUserDeleteResponseSchema);
}
