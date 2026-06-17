import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { UpstreamProxySettings } from "@/features/settings/components/upstream-proxy-settings";
import { createUpstreamProxyAdmin } from "@/test/mocks/factories";

function renderSettings(overrides: Partial<Parameters<typeof UpstreamProxySettings>[0]> = {}) {
  const props = {
    admin: createUpstreamProxyAdmin(),
    busy: false,
    onSaveSettings: vi.fn().mockResolvedValue(undefined),
    onCreateEndpoint: vi.fn().mockResolvedValue(undefined),
    onCreatePool: vi.fn().mockResolvedValue(undefined),
    onAddPoolMember: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };

  render(<UpstreamProxySettings {...props} />);
  return props;
}

describe("UpstreamProxySettings", () => {
  it("hides creation fields until a dialog is opened and shows trigger buttons", () => {
    renderSettings();

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Host")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Port")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Pool name")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Endpoint")).not.toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Add endpoint" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create pool" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add member" })).toBeInTheDocument();
  });

  it("lists configured endpoints and pools in the summary", () => {
    renderSettings();

    expect(screen.getByText("Primary proxy")).toBeInTheDocument();
    expect(screen.getByText(/proxy-primary\.test:8080/)).toBeInTheDocument();
    expect(screen.getByText("Primary pool")).toBeInTheDocument();
    expect(screen.getByText(/1 endpoint\(s\)/)).toBeInTheDocument();
  });

  it("shows explicit empty states when nothing is configured", () => {
    renderSettings({ admin: createUpstreamProxyAdmin({ endpoints: [], pools: [] }) });

    expect(screen.getByText("No proxy endpoints configured.")).toBeInTheDocument();
    expect(screen.getByText("No proxy pools configured.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add endpoint" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Create pool" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add member" })).toBeDisabled();
  });

  it("saves routing toggles and creates endpoints from a dialog", async () => {
    const user = userEvent.setup();
    const { onSaveSettings, onCreateEndpoint } = renderSettings();

    await user.click(screen.getByRole("switch", { name: "Enable upstream proxy routing" }));
    expect(onSaveSettings).toHaveBeenCalledWith({ upstreamProxyRoutingEnabled: true });

    await user.click(screen.getByRole("button", { name: "Add endpoint" }));
    const dialog = await screen.findByRole("dialog");

    await user.type(within(dialog).getByLabelText("Name"), "Backup proxy");
    await user.type(within(dialog).getByLabelText("Host"), "backup.proxy.test");
    await user.clear(within(dialog).getByLabelText("Port"));
    await user.type(within(dialog).getByLabelText("Port"), "8081");
    await user.click(within(dialog).getByRole("button", { name: "Create endpoint" }));

    await waitFor(() => {
      expect(onCreateEndpoint).toHaveBeenCalledWith({
        name: "Backup proxy",
        scheme: "http",
        host: "backup.proxy.test",
        port: 8081,
        username: null,
        password: null,
        isActive: true,
      });
    });

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("creates pools and blocks duplicate member submissions", async () => {
    const user = userEvent.setup();
    const { onCreatePool, onAddPoolMember } = renderSettings();

    await user.click(screen.getByRole("button", { name: "Create pool" }));
    const poolDialog = await screen.findByRole("dialog");

    await user.type(within(poolDialog).getByLabelText("Pool name"), "Codex pool");
    await user.click(within(poolDialog).getByRole("checkbox"));
    await user.click(within(poolDialog).getByRole("button", { name: "Create pool" }));

    await waitFor(() => {
      expect(onCreatePool).toHaveBeenCalledWith({
        name: "Codex pool",
        endpointIds: ["ep_primary"],
        isActive: true,
      });
    });

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Add member" }));
    const memberDialog = await screen.findByRole("dialog");

    expect(within(memberDialog).getByText(/Endpoint is already in Primary pool/)).toBeInTheDocument();
    expect(within(memberDialog).getByRole("button", { name: "Add member" })).toBeDisabled();
    expect(onAddPoolMember).not.toHaveBeenCalled();
  });
});
