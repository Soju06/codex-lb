import { screen, waitFor } from "@testing-library/react";
import { delay, HttpResponse, http } from "msw";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { RequestVisibilityDrawer } from "@/features/dashboard/components/request-visibility-drawer";
import {
  createDashboardSettings,
  createRequestLogEntry,
  createRequestLogVisibilityResponse,
} from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

describe("RequestVisibilityDrawer", () => {
  it("renders a loading state while the drawer query is pending", () => {
    server.use(
      http.get("/api/request-logs/:requestId/visibility", async () => {
        await delay(100);
        return HttpResponse.json(createRequestLogVisibilityResponse());
      }),
    );

    renderWithProviders(
      <RequestVisibilityDrawer
        request={createRequestLogEntry({ requestId: "req_1" })}
        open
        onOpenChange={() => {}}
      />,
    );

    expect(screen.getByText("Loading request visibility...")).toBeInTheDocument();
  });

  it("renders captured headers and body details", async () => {
    server.use(
      http.get("/api/request-logs/:requestId/visibility", () =>
        HttpResponse.json(
          createRequestLogVisibilityResponse({
            requestId: "req_1",
            truncated: true,
            body: {
              service_tier: "priority",
              reasoning: { effort: "high", summary: "detailed" },
              input: "hello",
              metadata: { sessionToken: "[REDACTED]" },
            },
          }),
        ),
      ),
    );

    renderWithProviders(
      <RequestVisibilityDrawer
        request={createRequestLogEntry({ requestId: "req_1", actualServiceTier: "priority", serviceTier: "priority" })}
        open
        onOpenChange={() => {}}
      />,
    );

    expect(await screen.findByText("Request headers")).toBeInTheDocument();
    expect(screen.getByText("Request body")).toBeInTheDocument();
    expect(screen.getAllByText("Fast").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Truncated")).toBeInTheDocument();
    expect(screen.getByText("Captured request metadata")).toBeInTheDocument();
    expect(screen.getByText("Requested tier")).toBeInTheDocument();
    expect(screen.getByText("Effective tier")).toBeInTheDocument();
    expect(screen.getByText("Reasoning effort")).toBeInTheDocument();
    expect(screen.getAllByText("Reasoning summary").length).toBeGreaterThan(0);
    expect(screen.getAllByText("priority").length).toBeGreaterThan(0);
    expect(screen.getAllByText("detailed").length).toBeGreaterThan(0);
    expect(screen.getByText(/sessionToken/)).toBeInTheDocument();
    expect(screen.getByText(/\[REDACTED\]/)).toBeInTheDocument();
  });

  it("renders an unavailable state for uncaptured requests", async () => {
    server.use(
      http.get("/api/request-logs/:requestId/visibility", () =>
        HttpResponse.json(
          createRequestLogVisibilityResponse({
            requestId: "req_2",
            captured: false,
            unavailableReason: "not_captured",
            headers: {},
            body: null,
          }),
        ),
      ),
    );

    renderWithProviders(
      <RequestVisibilityDrawer
        request={createRequestLogEntry({ requestId: "req_2" })}
        open
        onOpenChange={() => {}}
      />,
    );

    expect(await screen.findByText("Request visibility unavailable")).toBeInTheDocument();
    expect(await screen.findByText("Enable always")).toBeInTheDocument();
    expect(screen.getAllByText(/future requests/i).length).toBeGreaterThanOrEqual(1);
  });

  it("lets admins enable persistent capture for future requests", async () => {
    const user = userEvent.setup();

    server.use(
      http.get("/api/request-logs/:requestId/visibility", () =>
        HttpResponse.json(
          createRequestLogVisibilityResponse({
            requestId: "req_enable",
            captured: false,
            unavailableReason: "not_captured",
            headers: {},
            body: null,
          }),
        ),
      ),
    );

    renderWithProviders(
      <RequestVisibilityDrawer
        request={createRequestLogEntry({ requestId: "req_enable" })}
        open
        onOpenChange={() => {}}
      />,
    );

    await user.click(await screen.findByText("Enable always"));

    await waitFor(() => {
      expect(screen.queryByText("Enable always")).not.toBeInTheDocument();
    });
  });

  it("does not show enable controls when capture is already on", async () => {
    server.use(
      http.get("/api/settings", () =>
        HttpResponse.json(
          createDashboardSettings({
            requestVisibilityMode: "persistent",
            requestVisibilityExpiresAt: null,
            requestVisibilityEnabled: true,
          }),
        ),
      ),
      http.get("/api/request-logs/:requestId/visibility", () =>
        HttpResponse.json(
          createRequestLogVisibilityResponse({
            requestId: "req_enabled",
            captured: false,
            unavailableReason: "not_captured",
            headers: {},
            body: null,
          }),
        ),
      ),
    );

    renderWithProviders(
      <RequestVisibilityDrawer
        request={createRequestLogEntry({ requestId: "req_enabled" })}
        open
        onOpenChange={() => {}}
      />,
    );

    expect(await screen.findByText("Request visibility unavailable")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText("Enable always")).not.toBeInTheDocument();
    });
  });

  it("renders an API error state", async () => {
    server.use(
      http.get("/api/request-logs/:requestId/visibility", () =>
        HttpResponse.json(
          { error: { code: "request_visibility_failed", message: "Drawer load failed" } },
          { status: 500 },
        ),
      ),
    );

    renderWithProviders(
      <RequestVisibilityDrawer
        request={createRequestLogEntry({ requestId: "req_3" })}
        open
        onOpenChange={() => {}}
      />,
    );

    expect(await screen.findByText("Couldn't load request visibility")).toBeInTheDocument();
    expect(screen.getByText("Drawer load failed")).toBeInTheDocument();
  });

  it("renders request error details when the row has an error", async () => {
    server.use(
      http.get("/api/request-logs/:requestId/visibility", () =>
        HttpResponse.json(
          createRequestLogVisibilityResponse({
            requestId: "req_error",
            captured: false,
            unavailableReason: "not_captured",
            headers: {},
            body: null,
          }),
        ),
      ),
    );

    renderWithProviders(
      <RequestVisibilityDrawer
        request={createRequestLogEntry({
          requestId: "req_error",
          status: "error",
          errorCode: "rate_limit_exceeded",
          errorMessage: "Upstream rate limit reached",
        })}
        open
        onOpenChange={() => {}}
      />,
    );

    expect((await screen.findAllByText("Error")).length).toBeGreaterThan(0);
    expect(screen.getByText("Upstream rate limit reached")).toBeInTheDocument();
  });
});
