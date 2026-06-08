import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { UpstreamProxySettings } from "@/features/settings/components/upstream-proxy-settings";
import { createUpstreamProxyAdmin } from "@/test/mocks/factories";

describe("UpstreamProxySettings", () => {
  it("saves routing toggles and creates endpoints", async () => {
    const user = userEvent.setup();
    const onSaveSettings = vi.fn().mockResolvedValue(undefined);
    const onCreateEndpoint = vi.fn().mockResolvedValue(undefined);
    const admin = createUpstreamProxyAdmin();

    render(
      <UpstreamProxySettings
        admin={admin}
        busy={false}
        onSaveSettings={onSaveSettings}
        onCreateEndpoint={onCreateEndpoint}
        onCreatePool={vi.fn().mockResolvedValue(undefined)}
        onAddPoolMember={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await user.click(screen.getByRole("switch", { name: "Enable upstream proxy routing" }));
    expect(onSaveSettings).toHaveBeenCalledWith({ upstreamProxyRoutingEnabled: true });

    await user.type(screen.getByLabelText("Proxy endpoint name"), "Backup proxy");
    await user.type(screen.getByLabelText("Proxy endpoint host"), "backup.proxy.test");
    await user.clear(screen.getByLabelText("Proxy endpoint port"));
    await user.type(screen.getByLabelText("Proxy endpoint port"), "8081");
    await user.click(screen.getByRole("button", { name: "Create endpoint" }));

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

  it("creates pools and blocks duplicate member submissions", async () => {
    const user = userEvent.setup();
    const onCreatePool = vi.fn().mockResolvedValue(undefined);
    const onAddPoolMember = vi.fn().mockResolvedValue(undefined);
    const admin = createUpstreamProxyAdmin();

    render(
      <UpstreamProxySettings
        admin={admin}
        busy={false}
        onSaveSettings={vi.fn().mockResolvedValue(undefined)}
        onCreateEndpoint={vi.fn().mockResolvedValue(undefined)}
        onCreatePool={onCreatePool}
        onAddPoolMember={onAddPoolMember}
      />,
    );

    await user.type(screen.getByLabelText("Proxy pool name"), "Codex pool");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Create pool" }));

    expect(onCreatePool).toHaveBeenCalledWith({
      name: "Codex pool",
      endpointIds: ["ep_primary"],
      isActive: true,
    });
    expect(screen.getByText(/Endpoint is already in Primary pool/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add member" })).toBeDisabled();
    expect(onAddPoolMember).not.toHaveBeenCalled();
  });
});
