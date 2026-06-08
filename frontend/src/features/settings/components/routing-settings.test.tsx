import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RoutingSettings } from "@/features/settings/components/routing-settings";
import type { DashboardSettings } from "@/features/settings/schemas";
import { createAccountSummary } from "@/test/mocks/factories";

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
};

const BASE_SETTINGS: DashboardSettings = {
  stickyThreadsEnabled: false,
  upstreamStreamTransport: "default",
  upstreamProxyRoutingEnabled: false,
  upstreamProxyDefaultPoolId: null,
  preferEarlierResetAccounts: true,
  preferEarlierResetWindow: "secondary",
  routingStrategy: "usage_weighted",
  relativeAvailabilityPower: 2,
  relativeAvailabilityTopK: 5,
  singleAccountId: null,
  manualAccountPriorityIds: [],
  weeklyPaceWorkingDays: "0,1,2,3,4,5,6",
  openaiCacheAffinityMaxAgeSeconds: 300,
  dashboardSessionTtlSeconds: 43200,
  warmupModel: "gpt-5.4-mini",
  importWithoutOverwrite: false,
  totpRequiredOnLogin: false,
  totpConfigured: false,
  apiKeyAuthEnabled: true,
  additionalQuotaRoutingPolicies: {},
  additionalQuotaPolicies: [],
  ...LIMIT_WARMUP_DEFAULTS,
};

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
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "secondary",
      routingStrategy: "usage_weighted",
      relativeAvailabilityPower: 2,
      relativeAvailabilityTopK: 5,
      singleAccountId: null,
      openaiCacheAffinityMaxAgeSeconds: 180,
      dashboardSessionTtlSeconds: 43200,
      warmupModel: BASE_SETTINGS.warmupModel,
      weeklyPaceWorkingDays: BASE_SETTINGS.weeklyPaceWorkingDays,
      additionalQuotaRoutingPolicies: {},
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
      ...LIMIT_WARMUP_DEFAULTS,
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
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "secondary",
      routingStrategy: "usage_weighted",
      relativeAvailabilityPower: 2,
      relativeAvailabilityTopK: 5,
      singleAccountId: null,
      openaiCacheAffinityMaxAgeSeconds: 240,
      dashboardSessionTtlSeconds: 43200,
      warmupModel: BASE_SETTINGS.warmupModel,
      weeklyPaceWorkingDays: BASE_SETTINGS.weeklyPaceWorkingDays,
      additionalQuotaRoutingPolicies: {},
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
      ...LIMIT_WARMUP_DEFAULTS,
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
      stickyThreadsEnabled: true,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "secondary",
      routingStrategy: "usage_weighted",
      relativeAvailabilityPower: 2,
      relativeAvailabilityTopK: 5,
      singleAccountId: null,
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      warmupModel: BASE_SETTINGS.warmupModel,
      weeklyPaceWorkingDays: BASE_SETTINGS.weeklyPaceWorkingDays,
      additionalQuotaRoutingPolicies: {},
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
      ...LIMIT_WARMUP_DEFAULTS,
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
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "secondary",
      routingStrategy: "relative_availability",
      relativeAvailabilityPower: 1.5,
      relativeAvailabilityTopK: 5,
      singleAccountId: null,
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      warmupModel: BASE_SETTINGS.warmupModel,
      weeklyPaceWorkingDays: BASE_SETTINGS.weeklyPaceWorkingDays,
      additionalQuotaRoutingPolicies: {},
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
      ...LIMIT_WARMUP_DEFAULTS,
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
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "secondary",
      routingStrategy: "relative_availability",
      relativeAvailabilityPower: 2,
      relativeAvailabilityTopK: 6,
      singleAccountId: null,
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      warmupModel: BASE_SETTINGS.warmupModel,
      weeklyPaceWorkingDays: BASE_SETTINGS.weeklyPaceWorkingDays,
      additionalQuotaRoutingPolicies: {},
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
      ...LIMIT_WARMUP_DEFAULTS,
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
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "secondary",
      routingStrategy: "single_account",
      relativeAvailabilityPower: 2,
      relativeAvailabilityTopK: 5,
      singleAccountId: "acc-two",
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      warmupModel: BASE_SETTINGS.warmupModel,
      weeklyPaceWorkingDays: BASE_SETTINGS.weeklyPaceWorkingDays,
      additionalQuotaRoutingPolicies: {},
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
      ...LIMIT_WARMUP_DEFAULTS,
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

    await user.click(screen.getAllByRole("combobox")[1]);
    await user.click(await screen.findByRole("option", { name: "Single account" }));

    expect(onSave).toHaveBeenCalledWith({
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "secondary",
      routingStrategy: "single_account",
      relativeAvailabilityPower: 2,
      relativeAvailabilityTopK: 5,
      singleAccountId: "acc-one",
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      warmupModel: BASE_SETTINGS.warmupModel,
      weeklyPaceWorkingDays: BASE_SETTINGS.weeklyPaceWorkingDays,
      additionalQuotaRoutingPolicies: {},
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
      ...LIMIT_WARMUP_DEFAULTS,
    });
  });

  it("seeds account priority when ordered fallback routing is selected", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, routingStrategy: "capacity_weighted" }}
        accounts={[createAccountSummary({ accountId: "acc-one", email: "one@example.com", displayName: "one@example.com" })]}
        busy={false}
        onSave={onSave}
      />,
    );

    await user.click(screen.getAllByRole("combobox")[1]);
    await user.click(await screen.findByRole("option", { name: "Ordered fallback" }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        routingStrategy: "ordered_fallback",
        manualAccountPriorityIds: ["acc-one"],
      }),
    );
  });

  it("names limit warm-up controls for assistive technology", () => {
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    expect(screen.getByRole("switch", { name: "Enable limit warm-up" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Prefer earlier reset accounts" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Reset preference window" })).toBeInTheDocument();
    expect(screen.getByLabelText("Warmup model")).toHaveAttribute("maxLength", "128");
    expect(screen.getByLabelText("Warm-up model")).toHaveAttribute("maxLength", "128");
    expect(screen.getByLabelText("Warm-up prompt")).toHaveAttribute("maxLength", "512");
  });

  it("saves weekly pace working-day changes", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    await user.click(screen.getByRole("checkbox", { name: "Use Sat in weekly pace" }));

    expect(onSave).toHaveBeenCalledWith({
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "secondary",
      routingStrategy: "usage_weighted",
      relativeAvailabilityPower: 2,
      relativeAvailabilityTopK: 5,
      singleAccountId: null,
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      warmupModel: BASE_SETTINGS.warmupModel,
      weeklyPaceWorkingDays: "0,1,2,3,4,6",
      additionalQuotaRoutingPolicies: {},
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
      ...LIMIT_WARMUP_DEFAULTS,
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
      stickyThreadsEnabled: false,
      upstreamStreamTransport: "default",
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "primary",
      routingStrategy: "usage_weighted",
      relativeAvailabilityPower: 2,
      relativeAvailabilityTopK: 5,
      singleAccountId: null,
      openaiCacheAffinityMaxAgeSeconds: 300,
      dashboardSessionTtlSeconds: 43200,
      warmupModel: BASE_SETTINGS.warmupModel,
      weeklyPaceWorkingDays: BASE_SETTINGS.weeklyPaceWorkingDays,
      additionalQuotaRoutingPolicies: {},
      importWithoutOverwrite: false,
      totpRequiredOnLogin: false,
      apiKeyAuthEnabled: true,
      ...LIMIT_WARMUP_DEFAULTS,
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
});
