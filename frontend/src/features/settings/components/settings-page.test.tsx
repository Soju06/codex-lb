import { act, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { SettingsPage } from "@/features/settings/components/settings-page";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import type { DashboardSettings } from "@/features/settings/schemas";
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
const dataRetentionSettingsMock = vi.fn();
const telemetrySettingsMock = vi.fn();

vi.mock("@/features/settings/hooks/use-settings", () => ({
  useSettings: () => useSettingsMock(),
  useUpstreamProxyAdmin: () => useUpstreamProxyAdminMock(),
}));

vi.mock("@/features/accounts/hooks/use-accounts", () => ({
  useAccounts: () => useAccountsMock(),
}));

vi.mock("@/features/settings/components/settings-skeleton", () => ({
  SettingsSkeleton: () => <div data-testid="settings-skeleton" />,
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

vi.mock("@/features/settings/components/data-retention-settings", () => ({
  DataRetentionSettings: (props: unknown) => {
    dataRetentionSettingsMock(props);
    return <div>Data Retention Settings</div>;
  },
}));

vi.mock("@/features/settings/components/telemetry-settings", () => ({
  TelemetrySettings: (props: unknown) => {
    telemetrySettingsMock(props);
    return <div>Telemetry Settings</div>;
  },
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

type SettingsQueryState = {
  data: DashboardSettings | undefined;
  error: unknown;
  isPending: boolean;
  isFetching: boolean;
  refetch: Mock;
};

describe("SettingsPage", () => {
  const settings = createDashboardSettings();
  const upstreamAdmin = { endpoints: [], pools: [], bindings: [], routingEnabled: false, defaultPoolId: null };

  function mockSettingsQuery(settingsQuery: SettingsQueryState) {
    useSettingsMock.mockReturnValue({
      settingsQuery,
      updateSettingsMutation: {
        isPending: false,
        error: null,
        mutateAsync: vi.fn().mockResolvedValue(undefined),
      },
    });
  }

  beforeEach(() => {
    useAuthStore.setState({
      authMode: "standard",
      passwordManagementEnabled: true,
      passwordSessionActive: false,
      canWrite: true,
    });

    mockSettingsQuery({
      data: settings,
      error: null,
      isPending: false,
      isFetching: false,
      refetch: vi.fn().mockResolvedValue(undefined),
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
    modelSourcesSettingsMock.mockReset();
    dataRetentionSettingsMock.mockReset();
    telemetrySettingsMock.mockReset();
  });

  function renderSettings(initialEntry = "/settings") {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const renderTree = () => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <SettingsPage />
        </MemoryRouter>
      </QueryClientProvider>
    );
    const rendered = render(renderTree());
    return {
      ...rendered,
      rerenderSettings: () => {
        rendered.rerender(renderTree());
      },
    };
  }

  async function expandAdvancedSettings() {
    const user = userEvent.setup({ delay: null });
    await user.click(screen.getByRole("button", { name: "Show advanced settings" }));
  }

  it("keeps advanced sections collapsed and unmounted by default", () => {
    renderSettings();

    expect(screen.getByRole("button", { name: "Show advanced settings" })).toBeInTheDocument();
    expect(screen.queryByText("Routing Settings")).not.toBeInTheDocument();
    expect(screen.queryByText("Upstream Proxy Settings")).not.toBeInTheDocument();
    expect(screen.queryByText("Model Sources Settings")).not.toBeInTheDocument();
    expect(screen.queryByText("Firewall Section")).not.toBeInTheDocument();
    expect(screen.queryByText("Quota Planner Section")).not.toBeInTheDocument();
    expect(screen.queryByText("Sticky Sessions Section")).not.toBeInTheDocument();
    expect(screen.queryByText("Data Retention Settings")).not.toBeInTheDocument();
    expect(routingSettingsMock).not.toHaveBeenCalled();
    expect(upstreamProxySettingsMock).not.toHaveBeenCalled();
    expect(modelSourcesSettingsMock).not.toHaveBeenCalled();
    expect(firewallSectionMock).not.toHaveBeenCalled();
    expect(quotaPlannerSectionMock).not.toHaveBeenCalled();
    expect(stickySessionsSectionMock).not.toHaveBeenCalled();
    expect(dataRetentionSettingsMock).not.toHaveBeenCalled();

    // Core sections stay visible without any interaction.
    expect(screen.getByText("Appearance Settings")).toBeInTheDocument();
    expect(screen.getByText("Import Settings")).toBeInTheDocument();
    expect(screen.getByText("API Keys Section")).toBeInTheDocument();
    expect(screen.getByText("Telemetry Settings")).toBeInTheDocument();
  });

  it("mounts every advanced section after one expand interaction", async () => {
    renderSettings();

    await expandAdvancedSettings();

    expect(screen.getByText("Routing Settings")).toBeInTheDocument();
    expect(screen.getByText("Upstream Proxy Settings")).toBeInTheDocument();
    expect(screen.getByText("Model Sources Settings")).toBeInTheDocument();
    expect(screen.getByText("Firewall Section")).toBeInTheDocument();
    expect(screen.getByText("Quota Planner Section")).toBeInTheDocument();
    expect(screen.getByText("Sticky Sessions Section")).toBeInTheDocument();
    expect(screen.getByText("Data Retention Settings")).toBeInTheDocument();
  });

  it("disables write-capable sections for read-only guests", async () => {
    useAuthStore.setState({ canWrite: false });

    renderSettings();

    expect(screen.getByText("You are viewing the dashboard with read-only guest access. Admin controls are disabled.")).toBeInTheDocument();
    expect(screen.queryByText("Guest Access Settings")).not.toBeInTheDocument();
    expect(screen.queryByText("Password Settings")).not.toBeInTheDocument();
    expect(screen.queryByText("Session Settings")).not.toBeInTheDocument();
    expect(importSettingsMock).toHaveBeenCalledWith(expect.objectContaining({ busy: true }));
    expect(apiKeysSectionMock).toHaveBeenCalledWith(expect.objectContaining({ disabled: true }));
    expect(telemetrySettingsMock).toHaveBeenCalledWith(expect.objectContaining({ disabled: true }));

    await expandAdvancedSettings();

    expect(routingSettingsMock).toHaveBeenCalledWith(expect.objectContaining({ busy: true }));
    expect(upstreamProxySettingsMock).toHaveBeenCalledWith(expect.objectContaining({ busy: true }));
    expect(firewallSectionMock).toHaveBeenCalledWith(expect.objectContaining({ disabled: true }));
    expect(quotaPlannerSectionMock).toHaveBeenCalledWith(expect.objectContaining({ disabled: true }));
    expect(stickySessionsSectionMock).toHaveBeenCalledWith(expect.objectContaining({ disabled: true }));
    expect(dataRetentionSettingsMock).toHaveBeenCalledWith(expect.objectContaining({ busy: true }));
  });

  it("keeps guest access settings available for writable sessions", async () => {
    renderSettings();

    expect(screen.getByText("Guest Access Settings")).toBeInTheDocument();
    expect(guestAccessSettingsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        settings,
        busy: false,
      }),
    );

    await expandAdvancedSettings();

    expect(routingSettingsMock).toHaveBeenCalledWith(expect.objectContaining({ busy: false }));
  });

  it("shows an initial settings fetch error with retry", () => {
    mockSettingsQuery({
      data: undefined,
      error: new Error("load failed"),
      isPending: false,
      isFetching: false,
      refetch: vi.fn().mockResolvedValue(undefined),
    });

    renderSettings();

    expect(screen.getByRole("alert")).toHaveTextContent("load failed");
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(screen.queryByTestId("settings-skeleton")).not.toBeInTheDocument();
  });

  it("falls back to load-failure copy when the initial settings error has no message", () => {
    mockSettingsQuery({
      data: undefined,
      error: {},
      isPending: false,
      isFetching: false,
      refetch: vi.fn().mockResolvedValue(undefined),
    });

    renderSettings();

    expect(screen.getByRole("alert")).toHaveTextContent("Failed to load settings");
  });

  it("refetches settings when retry is activated after a failed initial load", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    mockSettingsQuery({
      data: undefined,
      error: new Error("load failed"),
      isPending: false,
      isFetching: false,
      refetch,
    });
    renderSettings();

    await userEvent.setup({ delay: null }).click(screen.getByRole("button", { name: "Retry" }));

    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("keeps retry visible and disabled while the settings refetch is in flight", async () => {
    let finishRefetch: (() => void) | undefined;
    const refetch = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishRefetch = resolve;
        }),
    );
    mockSettingsQuery({
      data: undefined,
      error: new Error("load failed"),
      isPending: false,
      isFetching: false,
      refetch,
    });

    const rendered = renderSettings();
    await userEvent.setup({ delay: null }).click(screen.getByRole("button", { name: "Retry" }));
    mockSettingsQuery({
      data: undefined,
      error: null,
      isPending: true,
      isFetching: true,
      refetch,
    });
    rendered.rerenderSettings();

    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("load failed");
    expect(screen.queryByTestId("settings-skeleton")).not.toBeInTheDocument();

    mockSettingsQuery({
      data: undefined,
      error: new Error("load failed"),
      isPending: false,
      isFetching: false,
      refetch,
    });
    expect(finishRefetch).toBeTypeOf("function");
    await act(async () => {
      finishRefetch?.();
    });
    rendered.rerenderSettings();

    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("keeps the skeleton while the first settings load is pending", () => {
    mockSettingsQuery({
      data: undefined,
      error: null,
      isPending: true,
      isFetching: true,
      refetch: vi.fn().mockResolvedValue(undefined),
    });

    renderSettings();

    expect(screen.getByTestId("settings-skeleton")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("keeps the settings form visible when a fetch error arrives with cached data", () => {
    mockSettingsQuery({
      data: settings,
      error: new Error("refresh failed"),
      isPending: false,
      isFetching: false,
      refetch: vi.fn().mockResolvedValue(undefined),
    });

    renderSettings();

    expect(screen.getByText("refresh failed")).toBeInTheDocument();
    expect(screen.getByText("Import Settings")).toBeInTheDocument();
    expect(screen.getByText("API Keys Section")).toBeInTheDocument();
    expect(screen.queryByTestId("settings-skeleton")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("expands Advanced and mounts firewall on the advanced deeplink", () => {
    renderSettings("/settings?advanced=1#firewall");

    expect(screen.getByText("Firewall Section")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hide advanced settings" })).toBeInTheDocument();
    expect(firewallSectionMock).toHaveBeenCalled();
  });

});
