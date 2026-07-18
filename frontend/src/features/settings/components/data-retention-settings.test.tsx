import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DataRetentionSettings } from "@/features/settings/components/data-retention-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import { createDashboardSettings } from "@/test/mocks/factories";

const baseSettings = createDashboardSettings();
const baseUpdatePayload = buildSettingsUpdateRequest(baseSettings, {});

describe("DataRetentionSettings", () => {
  it("shows stored overrides in the inputs", () => {
    render(
      <DataRetentionSettings
        settings={{
          ...baseSettings,
          requestLogRetentionDays: 90,
          usageHistoryRetentionDays: 45,
          requestLogRetentionOverrideDays: 90,
          usageHistoryRetentionOverrideDays: 45,
        }}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(screen.getByLabelText("Request log retention days")).toHaveDisplayValue("90");
    expect(screen.getByLabelText("Usage history retention days")).toHaveDisplayValue("45");
    expect(screen.getByRole("button", { name: "Save retention" })).toBeDisabled();
  });

  it("shows empty inputs with the inherited effective value as a hint while no override is set", () => {
    render(
      <DataRetentionSettings
        settings={{
          ...baseSettings,
          requestLogRetentionDays: 90,
          usageHistoryRetentionDays: 0,
          requestLogRetentionOverrideDays: null,
          usageHistoryRetentionOverrideDays: null,
        }}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(screen.getByLabelText("Request log retention days")).toHaveDisplayValue("");
    expect(screen.getByLabelText("Usage history retention days")).toHaveDisplayValue("");
    expect(
      screen.getByText("Inherited: 90 days (environment default; 0 = disabled)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Inherited: 0 days (environment default; 0 = disabled)"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save retention" })).toBeDisabled();
  });

  it("submits only the edited override field", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(<DataRetentionSettings settings={baseSettings} busy={false} onSave={onSave} />);

    const input = screen.getByLabelText("Request log retention days");
    await user.clear(input);
    await user.type(input, "30");
    await user.click(screen.getByRole("button", { name: "Save retention" }));

    expect(onSave).toHaveBeenCalledWith({
      ...baseUpdatePayload,
      requestLogRetentionOverrideDays: 30,
    });
    expect(onSave.mock.calls[0][0]).not.toHaveProperty("usageHistoryRetentionOverrideDays");
  });

  it("captures the inherited value as an override when typed deliberately", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(
      <DataRetentionSettings
        settings={{
          ...baseSettings,
          requestLogRetentionDays: 90, // effective via env alias
          requestLogRetentionOverrideDays: null,
        }}
        busy={false}
        onSave={onSave}
      />,
    );

    const input = screen.getByLabelText("Request log retention days");
    await user.type(input, "90");
    await user.click(screen.getByRole("button", { name: "Save retention" }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ requestLogRetentionOverrideDays: 90 }),
    );
  });

  it("clears an existing override by emptying the input (submits null)", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(
      <DataRetentionSettings
        settings={{
          ...baseSettings,
          requestLogRetentionDays: 120,
          requestLogRetentionOverrideDays: 120,
        }}
        busy={false}
        onSave={onSave}
      />,
    );

    const input = screen.getByLabelText("Request log retention days");
    await user.clear(input);
    await user.click(screen.getByRole("button", { name: "Save retention" }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ requestLogRetentionOverrideDays: null }),
    );
  });

  it("allows saving 0 to disable retention explicitly", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(
      <DataRetentionSettings
        settings={{
          ...baseSettings,
          usageHistoryRetentionDays: 45,
          usageHistoryRetentionOverrideDays: 45,
        }}
        busy={false}
        onSave={onSave}
      />,
    );

    const input = screen.getByLabelText("Usage history retention days");
    await user.clear(input);
    await user.type(input, "0");
    await user.click(screen.getByRole("button", { name: "Save retention" }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ usageHistoryRetentionOverrideDays: 0 }),
    );
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
