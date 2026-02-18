import { z } from "zod";

export const DashboardApiErrorDetailSchema = z.object({
  code: z.string(),
  message: z.string(),
});

export const DashboardApiErrorSchema = z.object({
  error: DashboardApiErrorDetailSchema,
});

export const OpenAIApiErrorDetailSchema = z.object({
  message: z.string().optional(),
  type: z.string().optional(),
  code: z.string().optional(),
  param: z.string().optional(),
});

export const OpenAIApiErrorSchema = z.object({
  error: OpenAIApiErrorDetailSchema,
});

export const ApiErrorResponseSchema = z.union([
  DashboardApiErrorSchema,
  OpenAIApiErrorSchema,
]);

export type DashboardApiError = z.infer<typeof DashboardApiErrorSchema>;
export type OpenAIApiError = z.infer<typeof OpenAIApiErrorSchema>;
export type ApiErrorResponse = z.infer<typeof ApiErrorResponseSchema>;
