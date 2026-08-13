import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { shouldExpandAdvancedSettings } from "@/features/settings/advanced-settings-deeplink";
import { AdvancedSettingsGroup } from "@/features/settings/components/advanced-settings-group";

describe("shouldExpandAdvancedSettings", () => {
  it("stays collapsed for a plain settings URL", () => {
    expect(shouldExpandAdvancedSettings("", "")).toBe(false);
    expect(shouldExpandAdvancedSettings("?view=guest", "")).toBe(false);
  });

  it("opens for the advanced query or firewall hash", () => {
    expect(shouldExpandAdvancedSettings("?advanced=1", "")).toBe(true);
    expect(shouldExpandAdvancedSettings("", "#firewall")).toBe(true);
    expect(shouldExpandAdvancedSettings("?advanced=1", "#firewall")).toBe(true);
  });
});

describe("AdvancedSettingsGroup", () => {
  it("scrolls once after preceding layout queries settle", async () => {
    let resolveLayoutQuery: ((value: string) => void) | undefined;
    let resolveUnrelatedQuery: ((value: string) => void) | undefined;
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const layoutQuery = queryClient.fetchQuery({
      queryKey: ["advanced-settings-layout"],
      queryFn: () =>
        new Promise<string>((resolve) => {
          resolveLayoutQuery = resolve;
        }),
    });
    const unrelatedQuery = queryClient.fetchQuery({
      queryKey: ["firewall", "list"],
      queryFn: () =>
        new Promise<string>((resolve) => {
          resolveUnrelatedQuery = resolve;
        }),
    });
    const scrollIntoView = vi.fn();
    const elementLookup = vi
      .spyOn(document, "getElementById")
      .mockReturnValue({ scrollIntoView } as unknown as HTMLElement);
    const animationFrame = vi
      .spyOn(window, "requestAnimationFrame")
      .mockImplementation((callback) => {
        callback(0);
        return 1;
      });

    const view = render(
      <QueryClientProvider client={queryClient}>
        <AdvancedSettingsGroup
          defaultOpen
          scrollToId="firewall"
          waitForQueryKeys={[["advanced-settings-layout"]]}
        >
          <div id="firewall">Firewall</div>
        </AdvancedSettingsGroup>
      </QueryClientProvider>,
    );

    expect(scrollIntoView).not.toHaveBeenCalled();

    await act(async () => {
      resolveLayoutQuery?.("ready");
      await layoutQuery;
    });

    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));

    resolveUnrelatedQuery?.("ready");
    await unrelatedQuery;

    await queryClient.fetchQuery({
      queryKey: ["later-refresh"],
      queryFn: async () => "ready",
    });
    expect(scrollIntoView).toHaveBeenCalledTimes(1);

    view.unmount();

    animationFrame.mockRestore();
    elementLookup.mockRestore();
  });
});
