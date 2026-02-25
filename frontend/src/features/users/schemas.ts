import { z } from "zod";

export const DashboardUserRoleSchema = z.enum(["admin", "user"]);

export const DashboardUserSchema = z.object({
  id: z.string(),
  username: z.string(),
  role: DashboardUserRoleSchema,
  isActive: z.boolean(),
  createdAt: z.string().datetime({ offset: true }),
  updatedAt: z.string().datetime({ offset: true }),
});

export const DashboardUsersResponseSchema = z.object({
  users: z.array(DashboardUserSchema),
});

export const DashboardUserCreateRequestSchema = z.object({
  username: z.string().min(1).max(128),
  password: z.string().min(8).max(256),
  role: DashboardUserRoleSchema,
});

export const DashboardUserUpdateRequestSchema = z.object({
  username: z.string().min(1).max(128).optional(),
  password: z.string().min(8).max(256).optional(),
  role: DashboardUserRoleSchema.optional(),
  isActive: z.boolean().optional(),
});

export const DashboardUserDeleteResponseSchema = z.object({
  status: z.string(),
});

export type DashboardUserRole = z.infer<typeof DashboardUserRoleSchema>;
export type DashboardUser = z.infer<typeof DashboardUserSchema>;
export type DashboardUserCreateRequest = z.infer<typeof DashboardUserCreateRequestSchema>;
export type DashboardUserUpdateRequest = z.infer<typeof DashboardUserUpdateRequestSchema>;
