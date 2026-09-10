import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConversationArchiveSettings } from "@/features/settings/components/conversation-archive-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings } from "@/features/settings/schemas";
import { createDashboardSettings } from "@/test/mocks/factories";

const DEFAULT_PROVENANCE = {
  conversation_archive_enabled: { source: "default" as const, envValue: false, default: false },
};

function renderSettings(settings: DashboardSettings) {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<ConversationArchiveSettings settings={settings} busy={false} onSave={onSave} />);
  return onSave;
}

describe("ConversationArchiveSettings", () => {
  it("renders the switch off with its inheritance badge and the read-only per-replica directory", () => {
    renderSettings(
      createDashboardSettings({
        conversationArchiveEnabled: false,
        conversationArchiveDir: "/srv/codex-lb/conversation-archive",
        provenance: DEFAULT_PROVENANCE,
      }),
    );

    expect(screen.getByText("Conversation archive")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Archive upstream conversations" })).not.toBeChecked();
    expect(screen.getByText("Default (off)")).toBeInTheDocument();
    expect(screen.getByText("/srv/codex-lb/conversation-archive")).toBeInTheDocument();
    expect(screen.getByText(/per-replica local shard/i)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("cannot enable the archive without confirming the dialog", async () => {
    const user = userEvent.setup();
    const settings = createDashboardSettings({ conversationArchiveEnabled: false, provenance: DEFAULT_PROVENANCE });
    const onSave = renderSettings(settings);

    await user.click(screen.getByRole("switch", { name: "Archive upstream conversations" }));

    // Nothing is saved until the operator confirms; the dialog spells out the consequence.
    expect(onSave).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("All prompt/response bodies will be written to each replica's local archive directory");

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Archive upstream conversations" })).not.toBeChecked();
  });

  it("stores the dashboard value true only through the dialog's confirm action", async () => {
    const user = userEvent.setup();
    const settings = createDashboardSettings({ conversationArchiveEnabled: false, provenance: DEFAULT_PROVENANCE });
    const onSave = renderSettings(settings);

    await user.click(screen.getByRole("switch", { name: "Archive upstream conversations" }));
    await user.click(await screen.findByRole("button", { name: "Enable recording" }));

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith(
      buildSettingsUpdateRequest(settings, { conversationArchiveEnabled: true }),
    );
  });

  it("turns the archive off immediately and shows the recording notice while it is on", async () => {
    const user = userEvent.setup();
    const settings = createDashboardSettings({
      conversationArchiveEnabled: true,
      provenance: {
        conversation_archive_enabled: { source: "dashboard" as const, envValue: false, default: false },
      },
    });
    const onSave = renderSettings(settings);

    expect(screen.getByRole("status")).toHaveTextContent("Recording is on");

    await user.click(screen.getByRole("switch", { name: "Archive upstream conversations" }));

    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(onSave).toHaveBeenCalledWith(
      buildSettingsUpdateRequest(settings, { conversationArchiveEnabled: false }),
    );
  });

  it("blocks reset to inherited when the environment alias would start recording", () => {
    renderSettings(
      createDashboardSettings({
        conversationArchiveEnabled: false,
        provenance: {
          conversation_archive_enabled: { source: "dashboard" as const, envValue: true, default: false },
        },
      }),
    );

    expect(screen.getByRole("button", { name: "Reset to inherited" })).toBeDisabled();
    expect(screen.getByText(/Resetting would enable recording/)).toBeInTheDocument();
  });

  it("resets a dashboard-owned off value to inherited with an explicit null when inheriting keeps it off", async () => {
    const user = userEvent.setup();
    const settings = createDashboardSettings({
      conversationArchiveEnabled: true,
      provenance: {
        conversation_archive_enabled: { source: "dashboard" as const, envValue: false, default: false },
      },
    });
    const onSave = renderSettings(settings);

    await user.click(screen.getByRole("button", { name: "Reset to inherited" }));

    const payload = buildSettingsUpdateRequest(settings, { conversationArchiveEnabled: null });
    expect(payload.conversationArchiveEnabled).toBeNull();
    expect(onSave).toHaveBeenCalledWith(payload);
  });

  it("does not send the toggle when an unrelated setting is saved", () => {
    const settings = createDashboardSettings({ provenance: DEFAULT_PROVENANCE });
    const payload = buildSettingsUpdateRequest(settings, { warmupModel: "gpt-5.6-sol" });
    expect("conversationArchiveEnabled" in payload).toBe(false);
  });
});
