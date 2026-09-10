import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DataRetentionSettings } from "@/features/settings/components/data-retention-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import { createDashboardSettings } from "@/test/mocks/factories";

const baseSettings = createDashboardSettings();
const baseUpdatePayload = buildSettingsUpdateRequest(baseSettings, {});

describe("DataRetentionSettings", () => {
  it("explains disabled request-log pruning neutrally without changing policy", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(
      <DataRetentionSettings
        settings={{
          ...baseSettings,
          requestLogRetentionDays: 0,
          requestLogRetentionOverrideDays: null,
        }}
        busy={false}
        onSave={onSave}
      />,
    );

    const disabledInfo = screen.getByText(
      /Request log pruning is disabled; logs are retained indefinitely and storage will grow over time/i,
    );
    expect(disabledInfo).toHaveClass("text-muted-foreground");
    expect(screen.getByRole("button", { name: "Use 30 days" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use 90 days" })).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("keeps disabled-state presets local until the operator explicitly saves", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(
      <DataRetentionSettings
        settings={{
          ...baseSettings,
          requestLogRetentionDays: 0,
          requestLogRetentionOverrideDays: null,
          usageHistoryRetentionOverrideDays: 45,
        }}
        busy={false}
        onSave={onSave}
      />,
    );

    const requestLogInput = screen.getByLabelText("Request log retention days");
    await user.click(screen.getByRole("button", { name: "Use 30 days" }));
    expect(requestLogInput).toHaveDisplayValue("30");
    expect(onSave).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Use 90 days" }));
    expect(requestLogInput).toHaveDisplayValue("90");
    expect(onSave).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Save retention" }));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ requestLogRetentionOverrideDays: 90 }),
    );
    expect(onSave.mock.calls[0][0]).not.toHaveProperty("usageHistoryRetentionOverrideDays");
  });

  it("does not show the disabled-state information or presets for an enabled effective policy", () => {
    render(
      <DataRetentionSettings
        settings={{
          ...baseSettings,
          requestLogRetentionDays: 30,
          requestLogRetentionOverrideDays: 30,
        }}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(
      screen.queryByText(/Request log pruning is disabled; logs are retained indefinitely/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use 30 days" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use 90 days" })).not.toBeInTheDocument();
  });

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

  it("shows empty inputs with the shared Default badge while no value is stored", () => {
    render(
      <DataRetentionSettings
        settings={{
          ...baseSettings,
          requestLogRetentionDays: 0,
          usageHistoryRetentionDays: 0,
          requestLogRetentionOverrideDays: null,
          usageHistoryRetentionOverrideDays: null,
          provenance: {
            request_log_retention_days: { source: "default", envValue: null, default: 0 },
            usage_history_retention_days: { source: "default", envValue: null, default: 0 },
          },
        }}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(screen.getByLabelText("Request log retention days")).toHaveDisplayValue("");
    expect(screen.getByLabelText("Usage history retention days")).toHaveDisplayValue("");
    expect(screen.getAllByText("Default (0)")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Reset to inherited" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Not configured/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save retention" })).toBeDisabled();
  });

  it("offers reset to inherited for a dashboard-owned window and clears only that field", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const settings = {
      ...baseSettings,
      requestLogRetentionDays: 90,
      usageHistoryRetentionDays: 0,
      requestLogRetentionOverrideDays: 90,
      usageHistoryRetentionOverrideDays: null,
      provenance: {
        request_log_retention_days: { source: "dashboard" as const, envValue: null, default: 0 },
        usage_history_retention_days: { source: "default" as const, envValue: null, default: 0 },
      },
    };

    render(<DataRetentionSettings settings={settings} busy={false} onSave={onSave} />);

    expect(screen.getByText("Default (0)")).toBeInTheDocument();
    const reset = screen.getByRole("button", { name: "Reset to inherited" });
    await user.click(reset);

    expect(onSave).toHaveBeenCalledWith(
      buildSettingsUpdateRequest(settings, { requestLogRetentionOverrideDays: null }),
    );
    expect(onSave.mock.calls[0][0]).not.toHaveProperty("usageHistoryRetentionOverrideDays");
  });

  it("renders no badge or hint against a backend that reports no provenance", () => {
    render(
      <DataRetentionSettings
        settings={{
          ...baseSettings,
          requestLogRetentionDays: 90,
          requestLogRetentionOverrideDays: null,
          provenance: undefined,
        }}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(screen.getByLabelText("Request log retention days")).toHaveDisplayValue("");
    expect(screen.queryByText(/Default \(/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Not configured/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset to inherited" })).not.toBeInTheDocument();
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
          requestLogRetentionDays: 90, // effective value reported by the server
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
