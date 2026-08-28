import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { TelemetrySettings } from "@/features/settings/components/telemetry-settings";
import i18n from "@/i18n";
import { createTelemetryConsent, createTelemetrySnapshotEnvelope } from "@/test/mocks/factories";
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
    expect(screen.getByText(i18n.t("settings.telemetry.optOutNotice"))).toBeInTheDocument();

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

  it("fetches the preview envelope only when the operator opens the dialog", async () => {
    const user = userEvent.setup();
    const telemetryRequests: URL[] = [];
    server.use(
      http.get("/api/settings/telemetry", ({ request }) => {
        const url = new URL(request.url);
        telemetryRequests.push(url);
        if (url.searchParams.get("include_preview") === "true") {
          return HttpResponse.json(
            createTelemetryConsent({ preview: createTelemetrySnapshotEnvelope() }),
          );
        }
        return HttpResponse.json(createTelemetryConsent());
      }),
    );

    renderWithProviders(<TelemetrySettings disabled={false} />);

    const viewButton = await screen.findByRole("button", { name: "View collected data" });
    await waitFor(() => expect(viewButton).toBeEnabled());
    // The always-on consent query must not carry the expensive preview flag.
    expect(telemetryRequests.length).toBeGreaterThan(0);
    expect(telemetryRequests.every((url) => !url.searchParams.has("include_preview"))).toBe(true);

    await user.click(viewButton);

    const dialog = await screen.findByRole("dialog", { name: "Collected telemetry data" });
    expect(within(dialog).getByText(/"schema_version": 1/)).toBeInTheDocument();
    expect(within(dialog).getByText(/"consent": "undecided"/)).toBeInTheDocument();
    expect(within(dialog).getByText(/"timestamp": "2026-08-06T00:00:00Z"/)).toBeInTheDocument();
    expect(
      telemetryRequests.filter((url) => url.searchParams.get("include_preview") === "true"),
    ).toHaveLength(1);
  });

  it.each(["Escape", "Close"] as const)(
    "returns focus to the exact preview invoker after %s dismissal",
    async (dismissal) => {
      const user = userEvent.setup();
      renderWithProviders(<TelemetrySettings disabled={false} />);

      const viewButton = await screen.findByRole("button", { name: "View collected data" });
      await waitFor(() => expect(viewButton).toBeEnabled());

      await user.click(viewButton);
      const dialog = await screen.findByRole("dialog", { name: "Collected telemetry data" });

      if (dismissal === "Escape") {
        await user.keyboard("{Escape}");
      } else {
        const closeButton = within(dialog)
          .getAllByRole("button", { name: "Close" })
          .find((button) => button.textContent === "Close");
        expect(closeButton).toBeDefined();
        await user.click(closeButton!);
      }

      await waitFor(() =>
        expect(screen.queryByRole("dialog", { name: "Collected telemetry data" })).not.toBeInTheDocument(),
      );
      expect(viewButton).toHaveFocus();
      expect(document.body).not.toHaveFocus();
    },
  );
});
