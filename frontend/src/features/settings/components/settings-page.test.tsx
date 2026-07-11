import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "@/features/settings/components/settings-page";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { createDashboardSettings } from "@/test/mocks/factories";

const useSettingsMock = vi.fn();
const useAccountsMock = vi.fn();
const useUpstreamProxyAdminMock = vi.fn();
const routingSettingsMock = vi.fn();
const upstreamProxySettingsMock = vi.fn();
const importSettingsMock = vi.fn();
const guestAccessSettingsMock = vi.fn();
const apiKeysSectionMock = vi.fn();
const firewallSectionMock = vi.fn();
const quotaPlannerSectionMock = vi.fn();
const stickySessionsSectionMock = vi.fn();
const modelSourcesSettingsMock = vi.fn();

vi.mock("@/features/settings/hooks/use-settings", () => ({
  useSettings: () => useSettingsMock(),
  useUpstreamProxyAdmin: () => useUpstreamProxyAdminMock(),
}));

vi.mock("@/features/accounts/hooks/use-accounts", () => ({
  useAccounts: () => useAccountsMock(),
}));

vi.mock("@/features/settings/components/appearance-settings", () => ({
  AppearanceSettings: () => <div>Appearance Settings</div>,
}));

vi.mock("@/features/settings/components/routing-settings", () => ({
  RoutingSettings: (props: unknown) => {
    routingSettingsMock(props);
    return <div>Routing Settings</div>;
  },
}));

vi.mock("@/features/settings/components/upstream-proxy-settings", () => ({
  UpstreamProxySettings: (props: unknown) => {
    upstreamProxySettingsMock(props);
    return <div>Upstream Proxy Settings</div>;
  },
}));

vi.mock("@/features/settings/components/import-settings", () => ({
  ImportSettings: (props: unknown) => {
    importSettingsMock(props);
    return <div>Import Settings</div>;
  },
}));

vi.mock("@/features/settings/components/guest-access-settings", () => ({
  GuestAccessSettings: (props: unknown) => {
    guestAccessSettingsMock(props);
    return <div>Guest Access Settings</div>;
  },
}));

vi.mock("@/features/settings/components/password-settings", () => ({
  PasswordSettings: () => <div>Password Settings</div>,
}));

vi.mock("@/features/settings/components/session-settings", () => ({
  SessionSettings: () => <div>Session Settings</div>,
}));

vi.mock("@/features/api-keys/components/api-keys-section", () => ({
  ApiKeysSection: (props: unknown) => {
    apiKeysSectionMock(props);
    return <div>API Keys Section</div>;
  },
}));

vi.mock("@/features/firewall/components/firewall-section", () => ({
  FirewallSection: (props: unknown) => {
    firewallSectionMock(props);
    return <div>Firewall Section</div>;
  },
}));

vi.mock("@/features/quota-planner/components/quota-planner-section", () => ({
  QuotaPlannerSection: (props: unknown) => {
    quotaPlannerSectionMock(props);
    return <div>Quota Planner Section</div>;
  },
}));

vi.mock("@/features/sticky-sessions/components/sticky-sessions-section", () => ({
  StickySessionsSection: (props: unknown) => {
    stickySessionsSectionMock(props);
    return <div>Sticky Sessions Section</div>;
  },
}));

vi.mock("@/features/model-sources/components/model-sources-settings", () => ({
  ModelSourcesSettings: (props: unknown) => {
    modelSourcesSettingsMock(props);
    return <div>Model Sources Settings</div>;
  },
}));

describe("SettingsPage", () => {
  const settings = createDashboardSettings();
  const upstreamAdmin = { endpoints: [], pools: [], bindings: [], routingEnabled: false, defaultPoolId: null };

  beforeEach(() => {
    useAuthStore.setState({
      authMode: "standard",
      passwordManagementEnabled: true,
      passwordSessionActive: false,
      canWrite: true,
    });

    useSettingsMock.mockReturnValue({
      settingsQuery: {
        data: settings,
        error: null,
        refetch: vi.fn().mockResolvedValue(undefined),
      },
      updateSettingsMutation: {
        isPending: false,
        error: null,
        mutateAsync: vi.fn().mockResolvedValue(undefined),
      },
    });
    useAccountsMock.mockReturnValue({
      accountsQuery: {
        data: [],
        isLoading: false,
      },
    });
    useUpstreamProxyAdminMock.mockReturnValue({
      upstreamProxyQuery: {
        data: upstreamAdmin,
        error: null,
      },
      createEndpointMutation: { isPending: false, error: null, mutateAsync: vi.fn() },
      createPoolMutation: { isPending: false, error: null, mutateAsync: vi.fn() },
      addPoolMemberMutation: { isPending: false, error: null, mutateAsync: vi.fn() },
      testEndpointMutation: { isPending: false, error: null, mutateAsync: vi.fn() },
    });

    routingSettingsMock.mockReset();
    upstreamProxySettingsMock.mockReset();
    importSettingsMock.mockReset();
    guestAccessSettingsMock.mockReset();
    apiKeysSectionMock.mockReset();
    firewallSectionMock.mockReset();
    quotaPlannerSectionMock.mockReset();
    stickySessionsSectionMock.mockReset();
  });

  it("disables write-capable sections for read-only guests", () => {
    useAuthStore.setState({ canWrite: false });

    render(<SettingsPage />);

    expect(screen.getByText("You are viewing the dashboard with read-only guest access. Admin controls are disabled.")).toBeInTheDocument();
    expect(screen.queryByText("Guest Access Settings")).not.toBeInTheDocument();
    expect(screen.queryByText("Password Settings")).not.toBeInTheDocument();
    expect(screen.queryByText("Session Settings")).not.toBeInTheDocument();
    expect(routingSettingsMock).toHaveBeenCalledWith(expect.objectContaining({ busy: true }));
    expect(upstreamProxySettingsMock).toHaveBeenCalledWith(expect.objectContaining({ busy: true }));
    expect(importSettingsMock).toHaveBeenCalledWith(expect.objectContaining({ busy: true }));
    expect(apiKeysSectionMock).toHaveBeenCalledWith(expect.objectContaining({ disabled: true }));
    expect(firewallSectionMock).toHaveBeenCalledWith(expect.objectContaining({ disabled: true }));
    expect(quotaPlannerSectionMock).toHaveBeenCalledWith(expect.objectContaining({ disabled: true }));
    expect(stickySessionsSectionMock).toHaveBeenCalledWith(expect.objectContaining({ disabled: true }));
  });

  it("keeps guest access settings available for writable sessions", () => {
    render(<SettingsPage />);

    expect(screen.getByText("Guest Access Settings")).toBeInTheDocument();
    expect(guestAccessSettingsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        settings,
        busy: false,
      }),
    );
    expect(routingSettingsMock).toHaveBeenCalledWith(expect.objectContaining({ busy: false }));
  });
});
