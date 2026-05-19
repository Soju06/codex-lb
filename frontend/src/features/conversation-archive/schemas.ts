import { z } from "zod";

export const ConversationArchiveFileSchema = z.object({
  name: z.string(),
  date: z.string().nullable(),
  sizeBytes: z.number().int().nonnegative(),
  compressed: z.boolean(),
  modifiedAt: z.string().datetime({ offset: true }),
});

export const ConversationArchiveRecordSchema = z.object({
  fileName: z.string().nullable().default(null),
  timestamp: z.string().datetime({ offset: true }).nullable(),
  requestId: z.string().nullable(),
  direction: z.string().nullable(),
  kind: z.string().nullable(),
  transport: z.string().nullable(),
  accountId: z.string().nullable(),
  method: z.string().nullable(),
  url: z.string().nullable(),
  statusCode: z.number().int().nullable(),
  headers: z.record(z.string(), z.string()).nullable(),
  payload: z.unknown(),
  extra: z.record(z.string(), z.unknown()).nullable().default(null),
});

export const ConversationArchiveRecordsResponseSchema = z.object({
  records: z.array(ConversationArchiveRecordSchema),
  total: z.number().int().nonnegative(),
  hasMore: z.boolean(),
});

export type ConversationArchiveFile = z.infer<typeof ConversationArchiveFileSchema>;
export type ConversationArchiveRecord = z.infer<typeof ConversationArchiveRecordSchema>;
export type ConversationArchiveRecordsResponse = z.infer<typeof ConversationArchiveRecordsResponseSchema>;
