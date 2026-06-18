import { z } from "zod";

export const RuntimeVersionSchema = z.object({
  currentVersion: z.string(),
  latestVersion: z.string().nullable().optional(),
  updateAvailable: z.boolean(),
  checkedAt: z.string(),
  source: z.string().nullable().optional(),
  releaseUrl: z.url(),
});

export type RuntimeVersion = z.infer<typeof RuntimeVersionSchema>;
