import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RoutingSettings } from "@/features/settings/components/routing-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings } from "@/features/settings/schemas";
import { createAccountSummary, createDashboardSettings } from "@/test/mocks/factories";

if (!HTMLElement.prototype.hasPointerCapture) {
  HTMLElement.prototype.hasPointerCapture = () => false;
}
if (!HTMLElement.prototype.setPointerCapture) {
  HTMLElement.prototype.setPointerCapture = () => undefined;
}
if (!HTMLElement.prototype.releasePointerCapture) {
  HTMLElement.prototype.releasePointerCapture = () => undefined;
}
if (!HTMLElement.prototype.scrollIntoView) {
  HTMLElement.prototype.scrollIntoView = () => undefined;
}

const LIMIT_WARMUP_DEFAULTS = {
  limitWarmupEnabled: false,
  limitWarmupWindows: "both" as const,
  limitWarmupModel: "auto",
  limitWarmupPrompt: "Say OK.",
  limitWarmupCooldownSeconds: 3600,
  limitWarmupMinAvailablePercent: 100,
  limitWarmupStaggeredIdleEnabled: false,
};

const BASE_SETTINGS: DashboardSettings = {
  ...createDashboardSettings(),
  ...LIMIT_WARMUP_DEFAULTS,
  stickyThreadsEnabled: false,
  preferEarlierResetAccounts: true,
  totpConfigured: false,
};
const BASE_UPDATE_PAYLOAD = buildSettingsUpdateRequest(BASE_SETTINGS, {});

describe("RoutingSettings", () => {
  it("saves a new prompt-cache affinity ttl from the button and Enter key", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />,
    );

    const ttlInput = screen.getByRole("spinbutton", { name: "Prompt-cache affinity TTL" });
    await user.clear(ttlInput);
    await user.type(ttlInput, "180");
    await user.click(screen.getByRole("button", { name: "Save TTL" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      stickyThreadsEnabled: false,
      openaiCacheAffinityMaxAgeSeconds: 180,
      guestAccessEnabled: false,
    });

    rerender(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, openaiCacheAffinityMaxAgeSeconds: 180 }}
        busy={false}
        onSave={onSave}
      />,
    );

    await user.clear(screen.getByRole("spinbutton", { name: "Prompt-cache affinity TTL" }));
    await user.type(screen.getByRole("spinbutton", { name: "Prompt-cache affinity TTL" }), "240{Enter}");

    expect(onSave).toHaveBeenLastCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      stickyThreadsEnabled: false,
      openaiCacheAffinityMaxAgeSeconds: 240,
      guestAccessEnabled: false,
    });
  });

  it("disables ttl save for invalid values and saves sticky-thread toggles", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    const ttlInput = screen.getByRole("spinbutton", { name: "Prompt-cache affinity TTL" });
    const saveButton = screen.getByRole("button", { name: "Save TTL" });
    expect(saveButton).toBeDisabled();

    await user.clear(ttlInput);
    await user.type(ttlInput, "0");
    expect(saveButton).toBeDisabled();

    await user.click(screen.getByRole("switch", { name: "Enable sticky threads" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      stickyThreadsEnabled: true,
      openaiCacheAffinityMaxAgeSeconds: 300,
      guestAccessEnabled: false,
    });
  });

  it("shows relative availability controls only for that strategy", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <RoutingSettings settings={{ ...BASE_SETTINGS, routingStrategy: "relative_availability" }} busy={false} onSave={onSave} />,
    );

    expect(screen.getByRole("spinbutton", { name: "Relative availability power" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Relative availability top K" })).toBeInTheDocument();

    await user.clear(screen.getByRole("spinbutton", { name: "Relative availability power" }));
    await user.type(screen.getByRole("spinbutton", { name: "Relative availability power" }), "1.5");
    await user.click(screen.getByRole("button", { name: "Save power" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      routingStrategy: "relative_availability",
      relativeAvailabilityPower: 1.5,
    });

    rerender(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);
    expect(screen.queryByRole("spinbutton", { name: "Relative availability power" })).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton", { name: "Relative availability top K" })).not.toBeInTheDocument();
  });

  it("saves additional quota routing policy overrides", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings
        settings={{
          ...BASE_SETTINGS,
          additionalQuotaRoutingPolicies: { "gpt-5.2-thinking": "inherit" },
        }}
        busy={false}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("combobox", { name: "gpt-5.2-thinking routing policy" }));
    await user.click(await screen.findByRole("option", { name: "Preserve" }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        additionalQuotaRoutingPolicies: { "gpt-5.2-thinking": "preserve" },
      }),
    );

    await user.type(screen.getByLabelText("Additional quota key"), "gpt-5.2-codex");
    await user.click(screen.getByRole("combobox", { name: "Additional quota routing policy" }));
    await user.click(await screen.findByRole("option", { name: "Burn first" }));
    await user.click(screen.getByRole("button", { name: "Save policy" }));

    expect(onSave).toHaveBeenLastCalledWith(
      expect.objectContaining({
        additionalQuotaRoutingPolicies: {
          "gpt-5.2-thinking": "inherit",
          "gpt-5.2-codex": "burn_first",
        },
      }),
    );
  });

  it("renders known additional quota policies without saved overrides", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings
        settings={{
          ...BASE_SETTINGS,
          additionalQuotaRoutingPolicies: {},
          additionalQuotaPolicies: [
            {
              quotaKey: "codex_spark",
              displayLabel: "GPT-5.3-Codex-Spark",
              routingPolicy: "burn_first",
              modelIds: ["gpt_5_3_codex_spark"],
            },
          ],
        }}
        busy={false}
        onSave={onSave}
      />,
    );

    expect(screen.getByText("GPT-5.3-Codex-Spark")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "codex_spark routing policy" }));
    await user.click(await screen.findByRole("option", { name: "Preserve" }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        additionalQuotaRoutingPolicies: { codex_spark: "preserve" },
      }),
    );
  });

  it("rejects decimal relative availability top K values", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings settings={{ ...BASE_SETTINGS, routingStrategy: "relative_availability" }} busy={false} onSave={onSave} />,
    );

    const topKInput = screen.getByRole("spinbutton", { name: "Relative availability top K" });
    const saveTopK = screen.getByRole("button", { name: "Save top K" });

    await user.clear(topKInput);
    await user.type(topKInput, "1.5");

    expect(saveTopK).toBeDisabled();

    await user.clear(topKInput);
    await user.type(topKInput, "6");
    await user.click(saveTopK);

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      routingStrategy: "relative_availability",
      relativeAvailabilityTopK: 6,
    });
  });

  it("saves warmup model updates", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    const warmupModelInput = screen.getByLabelText("Warmup model");
    await user.clear(warmupModelInput);
    await user.type(warmupModelInput, "gpt-5.4-pro");
    await user.click(screen.getByRole("button", { name: "Save warmup model" }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
      warmupModel: "gpt-5.4-pro",
      }),
    );
  });

  it("shows the configured upstream transport", () => {
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    expect(screen.getByText("Upstream stream transport")).toBeInTheDocument();
    expect(screen.getByText("Server default")).toBeInTheDocument();
  });

  it("shows account picker for single-account routing and saves the selected account", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, routingStrategy: "single_account" }}
        accounts={[
          createAccountSummary({ accountId: "acc-one", email: "one@example.com", displayName: "one@example.com" }),
          createAccountSummary({ accountId: "acc-two", email: "two@example.com", displayName: "two@example.com" }),
        ]}
        busy={false}
        onSave={onSave}
      />,
    );

    expect(screen.getByText("Selected account")).toBeInTheDocument();
    await user.click(screen.getByRole("combobox", { name: "Selected account" }));
    await user.click(await screen.findByRole("option", { name: /two@example.com/i }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      routingStrategy: "single_account",
      singleAccountId: "acc-two",
    });
  });

  it("excludes hard-blocked accounts from single-account routing choices", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, routingStrategy: "single_account" }}
        accounts={[
          createAccountSummary({
            accountId: "acc-active",
            email: "active@example.com",
            displayName: "active@example.com",
          }),
          createAccountSummary({
            accountId: "acc-reauth",
            email: "reauth@example.com",
            displayName: "reauth@example.com",
            status: "reauth_required",
          }),
          createAccountSummary({
            accountId: "acc-paused",
            email: "paused@example.com",
            displayName: "paused@example.com",
            status: "paused",
          }),
          createAccountSummary({
            accountId: "acc-deactivated",
            email: "deactivated@example.com",
            displayName: "deactivated@example.com",
            status: "deactivated",
          }),
        ]}
        busy={false}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("combobox", { name: "Selected account" }));

    expect(await screen.findByRole("option", { name: /active@example.com/i })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /reauth@example.com/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /paused@example.com/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /deactivated@example.com/i })).not.toBeInTheDocument();
  });

  it("saves an account together with single-account routing", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, routingStrategy: "capacity_weighted", singleAccountId: null }}
        accounts={[createAccountSummary({ accountId: "acc-one", email: "one@example.com", displayName: "one@example.com" })]}
        busy={false}
        onSave={onSave}
      />,
    );

    const routingStrategySelect = screen
      .getAllByRole("combobox")
      .find((element) => element.textContent?.includes("Capacity weighted"));
    if (!routingStrategySelect) {
      throw new Error("Routing strategy select not found");
    }
    await user.click(routingStrategySelect);
    await user.click(await screen.findByRole("option", { name: "Single account" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      routingStrategy: "single_account",
      singleAccountId: "acc-one",
    });
  });

  it("names limit warm-up controls for assistive technology", () => {
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    expect(screen.getByRole("switch", { name: "Enable limit warm-up" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Enable staggered idle warm-up" })).toBeDisabled();
    expect(screen.getByRole("switch", { name: "Prefer earlier reset accounts" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Reset preference window" })).toBeInTheDocument();
    expect(screen.getByLabelText("Warm-up model")).toHaveAttribute("maxLength", "128");
    expect(screen.getByRole("combobox", { name: "Warm-up windows" })).toBeInTheDocument();
    expect(screen.getByLabelText("Model")).toHaveAttribute("maxLength", "128");
    expect(screen.getByLabelText("Exhausted at %")).toHaveAttribute("max", "100");
    expect(screen.getByLabelText("Warm-up prompt")).toHaveAttribute("maxLength", "512");
  });

  it("saves weekly pace working-day changes", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    await user.click(screen.getByRole("checkbox", { name: "Use Sat in weekly pace" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      weeklyPaceWorkingDays: "0,1,2,3,4,6",
    });
  });

  it("keeps at least one weekly pace working day selected", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, weeklyPaceWorkingDays: "2" }}
        busy={false}
        onSave={onSave}
      />,
    );

    const onlyDay = screen.getByRole("checkbox", { name: "Use Wed in weekly pace" });
    expect(onlyDay).toBeDisabled();
    await user.click(onlyDay);

    expect(onSave).not.toHaveBeenCalled();
  });

  it("does not silently truncate decimal warm-up cooldown values", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    await user.clear(screen.getByLabelText("Warm-up cooldown"));
    await user.type(screen.getByLabelText("Warm-up cooldown"), "60.5");

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("saves warm-up exhausted threshold changes", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    await user.clear(screen.getByLabelText("Exhausted at %"));
    await user.type(screen.getByLabelText("Exhausted at %"), "98.5");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      limitWarmupExhaustedThresholdPercent: 98.5,
    });
  });

  it("rejects invalid warm-up exhausted thresholds", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    await user.clear(screen.getByLabelText("Exhausted at %"));
    await user.type(screen.getByLabelText("Exhausted at %"), "100.1");

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("saves the reset preference window", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
      configurable: true,
      value: () => false,
    });
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: () => undefined,
    });
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    await user.click(screen.getByRole("combobox", { name: "Reset preference window" }));
    await user.click(await screen.findByText("5h quota"));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      preferEarlierResetWindow: "primary",
    });
  });

  it("renders and saves the HTTP client routing policy", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    await user.click(screen.getByRole("combobox", { name: "HTTP client routing" }));
    await user.click(await screen.findByRole("option", { name: "Prefer persistent sessions" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      httpDownstreamTransportPolicy: "always_websocket",
    });
  });

  it("offers Fill first as a routing strategy option", () => {
    render(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, routingStrategy: "fill_first" }}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getAllByText("Fill first").length).toBeGreaterThan(0);
  });

  it("saves staggered idle warm-up when limit warm-up is enabled", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, limitWarmupEnabled: true }}
        busy={false}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("switch", { name: "Enable staggered idle warm-up" }));

    expect(onSave).toHaveBeenCalledWith(
      buildSettingsUpdateRequest(
        { ...BASE_SETTINGS, limitWarmupEnabled: true },
        { limitWarmupEnabled: true, limitWarmupStaggeredIdleEnabled: true },
      ),
    );
  });
});
