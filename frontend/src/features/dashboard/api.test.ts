import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { getConversationDetails, getRequestLogs } from "@/features/dashboard/api";
import { createConversationDetails } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";

describe("dashboard api", () => {
  it.each([".", ".."]) ("keeps dot-only conversation ID %s opaque", async (conversationId) => {
    const paths: string[] = [];
    server.use(
      http.get("/api/conversations/:conversationId", ({ request }) => {
        paths.push(new URL(request.url).pathname);
        return HttpResponse.json(createConversationDetails({ conversationId }));
      }),
    );

    const details = await getConversationDetails(conversationId);

    expect(paths).toEqual([`/api/conversations/%20${conversationId}`]);
    expect(details.conversationId).toBe(conversationId);
  });

  it("sends each request-log source as its own repeated query parameter", async () => {
    const queries: URLSearchParams[] = [];
    server.use(
      http.get("/api/request-logs", ({ request }) => {
        queries.push(new URL(request.url).searchParams);
        return HttpResponse.json({ requests: [], total: 0, hasMore: false });
      }),
    );

    await getRequestLogs({
      sources: ["subscription_overflow", "subscription_overflow_pinned"],
    });
    await getRequestLogs({ sources: [] });

    expect(queries[0]?.getAll("source")).toEqual([
      "subscription_overflow",
      "subscription_overflow_pinned",
    ]);
    expect(queries[1]?.has("source")).toBe(false);
  });
});
