import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { TelemetrySettings } from "@/features/settings/components/telemetry-settings";
import { createTelemetryConsent } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

describe("TelemetrySettings", () => {
  it("reflects the resolved state and persists a toggle change", async () => {
    const user = userEvent.setup();
    let putBody: unknown = null;
    server.use(
      http.put("/api/settings/telemetry", async ({ request }) => {
        putBody = await request.json();
        return HttpResponse.json(
          createTelemetryConsent({ state: "disabled", source: "persisted", active: false }),
        );
      }),
    );

    // Default mock state is enabled/persisted.
    renderWithProviders(<TelemetrySettings disabled={false} />);

    const toggle = await screen.findByRole("switch", { name: "Enable anonymous telemetry" });
    await waitFor(() => expect(toggle).toBeChecked());
    expect(toggle).toBeEnabled();

    await user.click(toggle);

    await waitFor(() => expect(putBody).toEqual({ enabled: false }));
  });

  it("disables the toggle and explains the environment override", async () => {
    server.use(
      http.get("/api/settings/telemetry", () =>
        HttpResponse.json(createTelemetryConsent({ state: "disabled", source: "env", active: false })),
      ),
    );

    renderWithProviders(<TelemetrySettings disabled={false} />);

    const toggle = await screen.findByRole("switch", { name: "Enable anonymous telemetry" });
    await waitFor(() =>
      expect(screen.getByText(/CODEX_LB_TELEMETRY_ENABLED/)).toBeInTheDocument(),
    );
    expect(toggle).toBeDisabled();
    expect(toggle).not.toBeChecked();
  });

  it("keeps the toggle disabled for read-only sessions", async () => {
    renderWithProviders(<TelemetrySettings disabled />);

    const toggle = await screen.findByRole("switch", { name: "Enable anonymous telemetry" });
    await waitFor(() => expect(toggle).toBeChecked());
    expect(toggle).toBeDisabled();
  });

  it("shows the collected payload preview on demand", async () => {
    const user = userEvent.setup();

    renderWithProviders(<TelemetrySettings disabled={false} />);

    const viewButton = await screen.findByRole("button", { name: "View collected data" });
    await waitFor(() => expect(viewButton).toBeEnabled());
    await user.click(viewButton);

    const dialog = await screen.findByRole("dialog", { name: "Collected telemetry data" });
    expect(within(dialog).getByText(/"schema_version": 1/)).toBeInTheDocument();
  });
});
