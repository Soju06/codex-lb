import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DataRetentionSettings } from "@/features/settings/components/data-retention-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import { createDashboardSettings } from "@/test/mocks/factories";

const baseSettings = createDashboardSettings();
const baseUpdatePayload = buildSettingsUpdateRequest(baseSettings, {});

describe("DataRetentionSettings", () => {
  it("shows the effective retention values", () => {
    render(
      <DataRetentionSettings
        settings={{ ...baseSettings, requestLogRetentionDays: 90, usageHistoryRetentionDays: 45 }}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(screen.getByLabelText("Request log retention days")).toHaveDisplayValue("90");
    expect(screen.getByLabelText("Usage history retention days")).toHaveDisplayValue("45");
    expect(screen.getByRole("button", { name: "Save retention" })).toBeDisabled();
  });

  it("submits only the edited retention field", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(<DataRetentionSettings settings={baseSettings} busy={false} onSave={onSave} />);

    const input = screen.getByLabelText("Request log retention days");
    await user.clear(input);
    await user.type(input, "30");
    await user.click(screen.getByRole("button", { name: "Save retention" }));

    expect(onSave).toHaveBeenCalledWith({
      ...baseUpdatePayload,
      requestLogRetentionDays: 30,
    });
    expect(onSave.mock.calls[0][0]).not.toHaveProperty("usageHistoryRetentionDays");
  });

  it("submits both fields when both are edited", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(<DataRetentionSettings settings={baseSettings} busy={false} onSave={onSave} />);

    const requestLogInput = screen.getByLabelText("Request log retention days");
    await user.clear(requestLogInput);
    await user.type(requestLogInput, "3650");
    const usageHistoryInput = screen.getByLabelText("Usage history retention days");
    await user.clear(usageHistoryInput);
    await user.type(usageHistoryInput, "45");
    await user.click(screen.getByRole("button", { name: "Save retention" }));

    expect(onSave).toHaveBeenCalledWith({
      ...baseUpdatePayload,
      requestLogRetentionDays: 3650,
      usageHistoryRetentionDays: 45,
    });
  });

  it("allows saving 0 to disable retention", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(
      <DataRetentionSettings
        settings={{ ...baseSettings, usageHistoryRetentionDays: 45 }}
        busy={false}
        onSave={onSave}
      />,
    );

    const input = screen.getByLabelText("Usage history retention days");
    await user.clear(input);
    await user.type(input, "0");
    await user.click(screen.getByRole("button", { name: "Save retention" }));

    expect(onSave).toHaveBeenCalledWith({
      ...baseUpdatePayload,
      usageHistoryRetentionDays: 0,
    });
  });

  it("rejects request-log values below the 30-day floor", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(<DataRetentionSettings settings={baseSettings} busy={false} onSave={onSave} />);

    const input = screen.getByLabelText("Request log retention days");
    await user.clear(input);
    await user.type(input, "7");

    expect(
      screen.getByText(/Request log retention must be 0 \(disabled\) or a whole number between 30 and 3650/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save retention" })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("rejects usage-history values below the 45-day floor and above the cap", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(<DataRetentionSettings settings={baseSettings} busy={false} onSave={onSave} />);

    const input = screen.getByLabelText("Usage history retention days");
    await user.clear(input);
    await user.type(input, "10");

    expect(
      screen.getByText(/Usage history retention must be 0 \(disabled\) or a whole number between 45 and 3650/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save retention" })).toBeDisabled();

    await user.clear(input);
    await user.type(input, "3651");
    expect(screen.getByRole("button", { name: "Save retention" })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("disables inputs while busy", () => {
    render(<DataRetentionSettings settings={baseSettings} busy={true} onSave={vi.fn()} />);
    expect(screen.getByLabelText("Request log retention days")).toBeDisabled();
    expect(screen.getByLabelText("Usage history retention days")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save retention" })).toBeDisabled();
  });
});
