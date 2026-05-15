import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SessionSettings } from "@/features/settings/components/session-settings";

const baseSettings = {
  stickyThreadsEnabled: true,
  upstreamStreamTransport: "default" as const,
  preferEarlierResetAccounts: false,
  preferEarlierResetWindow: "primary" as const,
  routingStrategy: "usage_weighted" as const,
  openaiCacheAffinityMaxAgeSeconds: 300,
  dashboardSessionTtlSeconds: 43200,
  importWithoutOverwrite: false,
  totpRequiredOnLogin: false,
  totpConfigured: true,
  apiKeyAuthEnabled: true,
};

describe("SessionSettings", () => {
  it("shows the current dashboard session lifetime in hours", () => {
    render(<SessionSettings settings={baseSettings} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);
    expect(screen.getByDisplayValue("12")).toBeInTheDocument();
  });

  it("saves a changed dashboard session lifetime in seconds", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(<SessionSettings settings={baseSettings} busy={false} onSave={onSave} />);

    const input = screen.getByLabelText("Dashboard session lifetime");
    await user.clear(input);
    await user.type(input, "24");
    await user.click(screen.getByRole("button", { name: "Save lifetime" }));

    expect(onSave).toHaveBeenCalledWith({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: false,
      preferEarlierResetWindow: "primary",
      routingStrategy: "usage_weighted",
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 86400,
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
    });
  });

  it("shows existing non-integer hour TTLs without rounding them down", () => {
    render(
      <SessionSettings
        settings={{ ...baseSettings, dashboardSessionTtlSeconds: 5400 }}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(screen.getByDisplayValue("1.50")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save lifetime" })).toBeDisabled();
  });

  it("rejects decimal hour input without silently truncating it", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(<SessionSettings settings={baseSettings} busy={false} onSave={onSave} />);

    const input = screen.getByLabelText("Dashboard session lifetime");
    await user.clear(input);
    await user.type(input, "1.5");

    expect(screen.getByRole("button", { name: "Save lifetime" })).toBeDisabled();
    expect(
      screen.getByText(/Enter a whole number of hours/i),
    ).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("shows a warning for lifetimes over 30 days and still allows saving", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(<SessionSettings settings={baseSettings} busy={false} onSave={onSave} />);

    const input = screen.getByLabelText("Dashboard session lifetime");
    await user.clear(input);
    await user.type(input, "8760");

    expect(
      screen.getByText(/Lifetimes over 30 days keep admin sessions valid for a long time/i),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save lifetime" }));

    expect(onSave).toHaveBeenCalledWith({
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: false,
      preferEarlierResetWindow: "primary",
      routingStrategy: "usage_weighted",
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 31536000,
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
    });
  });
});
