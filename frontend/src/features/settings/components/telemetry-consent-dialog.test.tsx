import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { TelemetryConsentDialog } from "@/features/settings/components/telemetry-consent-dialog";
import i18n from "@/i18n";
import type { TelemetryConsent } from "@/features/settings/schemas";
import { createTelemetryConsent, createTelemetryPreview } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

function undecidedConsent() {
  return createTelemetryConsent({ state: "undecided", source: "default", active: true });
}

// A persisted decision whose acknowledged notice version is behind: the
// backend attaches the preview once more so the operator sees the new payload.
function reinformedConsent(state: "enabled" | "disabled") {
  return createTelemetryConsent({
    state,
    source: "persisted",
    active: state === "enabled",
    preview: createTelemetryPreview(),
  });
}

describe("TelemetryConsentDialog", () => {
  beforeEach(() => {
    useAuthStore.setState({ canWrite: true });
  });

  it("shows both transmitted bodies with both decision actions while undecided", async () => {
    server.use(http.get("/api/settings/telemetry", () => HttpResponse.json(undecidedConsent())));

    renderWithProviders(<TelemetryConsentDialog />);

    const dialog = await screen.findByRole("dialog", { name: "Anonymous telemetry" });
    // The heartbeat is the exact transmitted envelope: top-level instance_id
    // and timestamp plus the snapshot under metrics.
    const heartbeat = within(dialog).getByRole("region", { name: "Heartbeat" });
    expect(heartbeat).toHaveTextContent('"instance_id": "00000000-0000-4000-8000-000000000000"');
    expect(heartbeat).toHaveTextContent('"timestamp": "2026-08-06T00:00:00Z"');
    expect(heartbeat).toHaveTextContent('"metrics": {');
    expect(heartbeat).toHaveTextContent('"schema_version": 2');
    expect(heartbeat).toHaveTextContent('"consent": "undecided"');
    expect(heartbeat).toHaveTextContent('"total": 2');
    // The completed-day body is shown alongside the heartbeat, not instead of it.
    const day = within(dialog).getByRole("region", { name: "Completed day" });
    expect(day).toHaveTextContent('"utc_date": "2026-08-05"');
    expect(day).toHaveTextContent('"sample_count": 3');
    expect(within(dialog).getByText(i18n.t("settings.telemetry.optOutNotice"))).toBeInTheDocument();
    expect(
      within(dialog).getByText(i18n.t("settings.telemetry.retentionNotice")),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Keep enabled" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Disable telemetry" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Got it" })).not.toBeInTheDocument();
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

  it.each(["enabled", "disabled"] as const)(
    "re-informs a persisted %s decision with an acknowledge-only dialog that never writes consent",
    async (state) => {
      const user = userEvent.setup();
      let putCalled = false;
      server.use(
        http.get("/api/settings/telemetry", () => HttpResponse.json(reinformedConsent(state))),
        http.put("/api/settings/telemetry", () => {
          putCalled = true;
          return HttpResponse.json(reinformedConsent(state));
        }),
      );

      renderWithProviders(<TelemetryConsentDialog />);

      const dialog = await screen.findByRole("dialog", { name: "Telemetry payload updated" });
      expect(
        within(dialog).getByText(i18n.t("settings.telemetry.noticeDialog.description")),
      ).toBeInTheDocument();
      expect(
        within(dialog).getByText(i18n.t("settings.telemetry.retentionNotice")),
      ).toBeInTheDocument();
      expect(within(dialog).getByRole("region", { name: "Heartbeat" })).toHaveTextContent(
        '"schema_version": 2',
      );
      expect(within(dialog).getByRole("region", { name: "Completed day" })).toHaveTextContent(
        '"utc_date": "2026-08-05"',
      );
      // No decision is offered: the persisted one stands as recorded.
      expect(within(dialog).queryByRole("button", { name: "Keep enabled" })).not.toBeInTheDocument();
      expect(
        within(dialog).queryByRole("button", { name: "Disable telemetry" }),
      ).not.toBeInTheDocument();

      await user.click(within(dialog).getByRole("button", { name: "Got it" }));

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(putCalled).toBe(false);
    },
  );

  it("keeps the notice open after a refetch returns no preview until the operator acknowledges", async () => {
    const user = userEvent.setup();
    let reads = 0;
    server.use(
      http.get("/api/settings/telemetry", () => {
        reads += 1;
        // Building the first preview acknowledges the notice server-side, so
        // every later read of the same installation carries preview: null.
        return HttpResponse.json(
          reads === 1
            ? reinformedConsent("enabled")
            : createTelemetryConsent({ state: "enabled", source: "persisted", active: true, preview: null }),
        );
      }),
    );

    const { queryClient } = renderWithProviders(<TelemetryConsentDialog />);

    const dialog = await screen.findByRole("dialog", { name: "Telemetry payload updated" });

    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["settings", "telemetry"] });
    });
    expect(reads).toBe(2);
    expect(
      queryClient.getQueryData<TelemetryConsent>(["settings", "telemetry"])?.preview,
    ).toBeNull();
    // The latched first preview keeps the one-time notice on screen.
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByRole("region", { name: "Heartbeat" })).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Got it" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("stays hidden for a persisted decision at the current notice version", async () => {
    // An acknowledged notice is a cheap read: the backend attaches no preview,
    // so there is nothing to show regardless of the persisted state.
    server.use(
      http.get("/api/settings/telemetry", () =>
        HttpResponse.json(
          createTelemetryConsent({ state: "enabled", source: "persisted", active: true, preview: null }),
        ),
      ),
    );

    const { queryClient } = renderWithProviders(<TelemetryConsentDialog />);

    await waitFor(() =>
      expect(queryClient.getQueryState(["settings", "telemetry"])?.status).toBe("success"),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("stays hidden when the response carries no preview", async () => {
    server.use(
      http.get("/api/settings/telemetry", () =>
        HttpResponse.json(
          createTelemetryConsent({ state: "undecided", source: "default", active: true, preview: null }),
        ),
      ),
    );

    const { queryClient } = renderWithProviders(<TelemetryConsentDialog />);

    await waitFor(() =>
      expect(queryClient.getQueryState(["settings", "telemetry"])?.status).toBe("success"),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each([
    ["undecided", false],
    ["enabled", true],
  ] as const)(
    "stays hidden while the environment variable controls telemetry (%s)",
    async (state, active) => {
      // Synthetic preview keeps the preview-null gate open so this test binds
      // the source !== "env" gate alone, for both dialog variants.
      server.use(
        http.get("/api/settings/telemetry", () =>
          HttpResponse.json(
            createTelemetryConsent({ state, source: "env", active, preview: createTelemetryPreview() }),
          ),
        ),
      );

      const { queryClient } = renderWithProviders(<TelemetryConsentDialog />);

      await waitFor(() =>
        expect(queryClient.getQueryState(["settings", "telemetry"])?.status).toBe("success"),
      );
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    },
  );

  it("stays hidden for read-only sessions and never requests the preview aggregation", async () => {
    useAuthStore.setState({ canWrite: false });
    let requested = false;
    server.use(
      http.get("/api/settings/telemetry", () => {
        requested = true;
        return HttpResponse.json(undecidedConsent());
      }),
    );

    const { queryClient } = renderWithProviders(<TelemetryConsentDialog />);

    // Read-only guests can never act on the dialog, so the consent query is
    // disabled entirely: no fetch fires and the query stays pending.
    await waitFor(() =>
      expect(queryClient.getQueryState(["settings", "telemetry"])?.fetchStatus).toBe("idle"),
    );
    expect(requested).toBe(false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
