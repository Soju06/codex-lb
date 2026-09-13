import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { InheritBadge } from "@/features/settings/components/inherit-badge";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import { createDashboardSettings } from "@/test/mocks/factories";

function renderBadge(provenance: Record<string, { source: "dashboard" | "env" | "default"; envValue: number; default: number }>) {
  const settings = createDashboardSettings({ provenance });
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(
    <InheritBadge
      settings={settings}
      name="proxy_account_stream_limit"
      field="proxyAccountStreamLimit"
      busy={false}
      onSave={onSave}
      fallbackValue={8}
    />,
  );
  return { settings, onSave };
}

describe("InheritBadge", () => {
  it("labels a value inherited from the environment", () => {
    renderBadge({ proxy_account_stream_limit: { source: "env", envValue: 12, default: 8 } });
    expect(screen.getByText("Inherited from environment (12)")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("labels a code default", () => {
    renderBadge({ proxy_account_stream_limit: { source: "default", envValue: 8, default: 8 } });
    expect(screen.getByText("Default (8)")).toBeInTheDocument();
  });

  it("offers a reset that clears the dashboard value with an explicit null", async () => {
    const user = userEvent.setup();
    const { settings, onSave } = renderBadge({
      proxy_account_stream_limit: { source: "dashboard", envValue: 8, default: 8 },
    });
    expect(screen.queryByText(/Inherited|Default/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Reset to inherited" }));

    expect(onSave).toHaveBeenCalledWith(buildSettingsUpdateRequest(settings, { proxyAccountStreamLimit: null }));
  });

  it("disables the reset with the reason when clearing would be rejected", () => {
    render(
      <InheritBadge
        settings={createDashboardSettings({
          provenance: { proxy_account_stream_limit: { source: "dashboard", envValue: 8, default: 8 } },
        })}
        name="proxy_account_stream_limit"
        field="proxyAccountStreamLimit"
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
        resetBlockedReason="reserve would exceed the limit"
      />,
    );
    expect(screen.getByRole("button", { name: "Reset to inherited" })).toBeDisabled();
    expect(screen.getByText("reserve would exceed the limit")).toBeInTheDocument();
  });

  it("renders nothing without provenance when no fallback value is given", () => {
    const { container } = render(
      <InheritBadge
        settings={createDashboardSettings()}
        name="proxy_account_stream_limit"
        field="proxyAccountStreamLimit"
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("falls back to the effective-value hint when the backend reports no provenance", () => {
    render(
      <InheritBadge
        settings={createDashboardSettings()}
        name="proxy_account_stream_limit"
        field="proxyAccountStreamLimit"
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
        fallbackValue={8}
      />,
    );
    expect(screen.getByText("Inherited effective value: 8")).toBeInTheDocument();
  });
});
