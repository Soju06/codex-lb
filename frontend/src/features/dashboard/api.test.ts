import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { getConversationDetails } from "@/features/dashboard/api";
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
});
