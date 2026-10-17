import { describe, expect, it } from "vitest";

import { ConversationArchiveRecordsResponseSchema } from "@/features/conversation-archive/schemas";

describe("conversation archive schemas", () => {
  it("parses records with arbitrary payloads", () => {
    const parsed = ConversationArchiveRecordsResponseSchema.parse({
      records: [
        {
          timestamp: "2026-04-29T10:00:00Z",
          fileName: "2026-04-29.jsonl.gz",
          requestId: "req_1",
          direction: "server_to_codex",
          kind: "responses",
          transport: "sse",
          accountId: "acc_1",
          method: "POST",
          url: "https://chatgpt.com/backend-api/codex/responses",
          statusCode: 200,
          headers: { authorization: "[redacted]" },
          payload: { type: "response.completed" },
          extra: null,
        },
      ],
      total: 1,
      hasMore: false,
    });

    expect(parsed.records[0]?.payload).toEqual({ type: "response.completed" });
  });
});
