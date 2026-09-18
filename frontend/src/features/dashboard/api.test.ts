import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { getConversationDetails, getDashboardRequestActivity } from "@/features/dashboard/api";
import { createConversationDetails } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";

describe("dashboard api", () => {
  it("loads bounded daily request activity", async () => {
    const paths: string[] = [];
    server.use(
      http.get("/api/dashboard/request-activity", ({ request }) => {
        paths.push(new URL(request.url).pathname);
        return HttpResponse.json({
          days: [{ date: "2026-01-01", requests: 12 }],
        });
      }),
    );

    const activity = await getDashboardRequestActivity();

    expect(paths).toEqual(["/api/dashboard/request-activity"]);
    expect(activity.days).toEqual([{ date: "2026-01-01", requests: 12 }]);
  });

  it("forwards a provided timezone to the request activity endpoint", async () => {
    let requestedTimezone: string | null = null;
    server.use(
      http.get("/api/dashboard/request-activity", ({ request }) => {
        requestedTimezone = new URL(request.url).searchParams.get("timezone");
        return HttpResponse.json({ days: [] });
      }),
    );

    await getDashboardRequestActivity({ timezone: "America/Los_Angeles" });

    expect(requestedTimezone).toBe("America/Los_Angeles");
  });

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
