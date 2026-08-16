import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { createTelemetryConsent } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

describe("telemetry consent flow integration", () => {
  it("shows the one-time consent dialog on dashboard entry and persists the decision", async () => {
    const user = userEvent.setup({ delay: null });
    let putBody: unknown = null;
    let consent = createTelemetryConsent({ state: "undecided", source: "default", active: true });
    server.use(
      http.get("/api/settings/telemetry", () => HttpResponse.json(consent)),
      http.put("/api/settings/telemetry", async ({ request }) => {
        putBody = await request.json();
        consent = createTelemetryConsent({ state: "disabled", source: "persisted", active: false });
        return HttpResponse.json(consent);
      }),
    );

    window.history.pushState({}, "", "/dashboard");
    renderWithProviders(<App />);

    const dialog = await screen.findByRole("dialog", { name: "Anonymous telemetry" });
    // The dialog renders the full transmitted envelope, not just the metrics.
    expect(dialog).toHaveTextContent('"instance_id": "00000000-0000-4000-8000-000000000000"');
    expect(dialog).toHaveTextContent('"timestamp": "2026-08-06T00:00:00Z"');
    expect(dialog).toHaveTextContent('"schema_version": 1');

    await user.click(screen.getByRole("button", { name: "Disable telemetry" }));

    await waitFor(() => expect(putBody).toEqual({ enabled: false }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("does not show the consent dialog when consent is already decided", async () => {
    window.history.pushState({}, "", "/dashboard");
    const { queryClient } = renderWithProviders(<App />);

    // Default mock state is enabled/persisted.
    await waitFor(() =>
      expect(queryClient.getQueryState(["settings", "telemetry"])?.status).toBe("success"),
    );
    expect(screen.queryByRole("dialog", { name: "Anonymous telemetry" })).not.toBeInTheDocument();
  });
});
