import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { TelemetryConsentDialog } from "@/features/settings/components/telemetry-consent-dialog";
import { createTelemetryConsent } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

function undecidedConsent() {
  return createTelemetryConsent({ state: "undecided", source: "default", active: true });
}

describe("TelemetryConsentDialog", () => {
  beforeEach(() => {
    useAuthStore.setState({ canWrite: true });
  });

  it("shows the exact payload preview with both decision actions while undecided", async () => {
    server.use(http.get("/api/settings/telemetry", () => HttpResponse.json(undecidedConsent())));

    renderWithProviders(<TelemetryConsentDialog />);

    const dialog = await screen.findByRole("dialog", { name: "Anonymous telemetry" });
    expect(within(dialog).getByText(/"schema_version": 1/)).toBeInTheDocument();
    expect(
      within(dialog).getByText(/"instance_id": "00000000-0000-4000-8000-000000000000"/),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Keep enabled" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Disable telemetry" })).toBeInTheDocument();
    expect(
      within(dialog).getByRole("link", { name: "Learn what is collected and why" }),
    ).toBeInTheDocument();
  });

  it("persists enabled=true when the operator keeps telemetry enabled", async () => {
    const user = userEvent.setup();
    let putBody: unknown = null;
    server.use(
      http.get("/api/settings/telemetry", () => HttpResponse.json(undecidedConsent())),
      http.put("/api/settings/telemetry", async ({ request }) => {
        putBody = await request.json();
        return HttpResponse.json(
          createTelemetryConsent({ state: "enabled", source: "persisted", active: true }),
        );
      }),
    );

    renderWithProviders(<TelemetryConsentDialog />);

    await user.click(await screen.findByRole("button", { name: "Keep enabled" }));

    await waitFor(() => expect(putBody).toEqual({ enabled: true }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("persists enabled=false when the operator disables telemetry", async () => {
    const user = userEvent.setup();
    let putBody: unknown = null;
    server.use(
      http.get("/api/settings/telemetry", () => HttpResponse.json(undecidedConsent())),
      http.put("/api/settings/telemetry", async ({ request }) => {
        putBody = await request.json();
        return HttpResponse.json(
          createTelemetryConsent({ state: "disabled", source: "persisted", active: false }),
        );
      }),
    );

    renderWithProviders(<TelemetryConsentDialog />);

    await user.click(await screen.findByRole("button", { name: "Disable telemetry" }));

    await waitFor(() => expect(putBody).toEqual({ enabled: false }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("closes without persisting a decision when dismissed with Escape", async () => {
    const user = userEvent.setup();
    let putCalled = false;
    server.use(
      http.get("/api/settings/telemetry", () => HttpResponse.json(undecidedConsent())),
      http.put("/api/settings/telemetry", () => {
        putCalled = true;
        return HttpResponse.json(undecidedConsent());
      }),
    );

    renderWithProviders(<TelemetryConsentDialog />);

    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(putCalled).toBe(false);
  });

  it("stays hidden once a decision has been persisted", async () => {
    // Default mock state is enabled/persisted.
    const { queryClient } = renderWithProviders(<TelemetryConsentDialog />);

    await waitFor(() =>
      expect(queryClient.getQueryState(["settings", "telemetry"])?.status).toBe("success"),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("stays hidden while the environment variable controls telemetry", async () => {
    server.use(
      http.get("/api/settings/telemetry", () =>
        HttpResponse.json(createTelemetryConsent({ state: "undecided", source: "env", active: false })),
      ),
    );

    const { queryClient } = renderWithProviders(<TelemetryConsentDialog />);

    await waitFor(() =>
      expect(queryClient.getQueryState(["settings", "telemetry"])?.status).toBe("success"),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("stays hidden for read-only sessions that cannot persist a decision", async () => {
    useAuthStore.setState({ canWrite: false });
    server.use(http.get("/api/settings/telemetry", () => HttpResponse.json(undecidedConsent())));

    const { queryClient } = renderWithProviders(<TelemetryConsentDialog />);

    await waitFor(() =>
      expect(queryClient.getQueryState(["settings", "telemetry"])?.status).toBe("success"),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
