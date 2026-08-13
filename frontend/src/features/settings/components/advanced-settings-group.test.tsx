import { render } from "@testing-library/react";
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
  it("repeats deeplink scrolling when async content changes the layout", () => {
    let resizeCallback: ResizeObserverCallback | undefined;
    const disconnect = vi.fn();
    const observe = vi.fn();
    const resizeObserver = vi
      .spyOn(globalThis, "ResizeObserver")
      .mockImplementation(
        class {
          constructor(callback: ResizeObserverCallback) {
            resizeCallback = callback;
          }
          observe = observe;
          unobserve = vi.fn();
          disconnect = disconnect;
        },
      );
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
      <AdvancedSettingsGroup defaultOpen scrollToId="firewall">
        <div id="firewall">Firewall</div>
      </AdvancedSettingsGroup>,
    );

    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(observe).toHaveBeenCalledTimes(1);

    resizeCallback?.([], {} as ResizeObserver);

    expect(scrollIntoView).toHaveBeenCalledTimes(2);
    view.unmount();
    expect(disconnect).toHaveBeenCalledTimes(1);

    animationFrame.mockRestore();
    elementLookup.mockRestore();
    resizeObserver.mockRestore();
  });
});
