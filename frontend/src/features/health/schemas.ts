import { z } from "zod";

export const HealthCheckResponseSchema = z.object({
  status: z.string(),
});
