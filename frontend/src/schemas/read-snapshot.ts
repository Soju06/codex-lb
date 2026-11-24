import { z } from "zod";
import { ApiError } from "@/lib/api-client";

export const ReadSnapshotSchema = z.object({
  generatedAt: z.string().nullable(),
  stale: z.boolean(),
  refreshing: z.boolean(),
  lastError: z.string().nullable(),
  generation: z.number(),
  refreshIntervalSeconds: z.number(),
});

export type ReadSnapshotMetadata = z.infer<typeof ReadSnapshotSchema>;

export function isSnapshotWarming(error: unknown): boolean {
  return error instanceof ApiError && error.code === "read_snapshot_warming";
}
