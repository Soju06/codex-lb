import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RoutingSettings } from "@/features/settings/components/routing-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings } from "@/features/settings/schemas";
import { createAccountSummary, createDashboardSettings, createModelSource } from "@/test/mocks/factories";

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
  limitWarmupIdleThresholdPercent: 1,
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
  it("saves per-account capacity limits including zero for unlimited", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    expect(screen.getByRole("spinbutton", { name: "Response-create limit" })).toHaveValue(4);
    expect(screen.getByRole("spinbutton", { name: "Stream limit" })).toHaveValue(8);
    expect(screen.getByRole("spinbutton", { name: "Stream recovery reserve" })).toHaveValue(1);
    expect(screen.getByRole("spinbutton", { name: "API key fair-share threshold (%)" })).toHaveValue(0);

    await user.clear(screen.getByRole("spinbutton", { name: "Response-create limit" }));
    await user.type(screen.getByRole("spinbutton", { name: "Response-create limit" }), "0");
    await user.clear(screen.getByRole("spinbutton", { name: "Stream limit" }));
    await user.type(screen.getByRole("spinbutton", { name: "Stream limit" }), "12");
    await user.clear(screen.getByRole("spinbutton", { name: "Stream recovery reserve" }));
    await user.type(screen.getByRole("spinbutton", { name: "Stream recovery reserve" }), "2");
    await user.clear(screen.getByRole("spinbutton", { name: "API key fair-share threshold (%)" }));
    await user.type(screen.getByRole("spinbutton", { name: "API key fair-share threshold (%)" }), "80");
    await user.click(screen.getByRole("button", { name: "Save capacity limits" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      proxyAccountResponseCreateLimit: 0,
      proxyAccountStreamLimit: 12,
      proxyAccountStreamRecoveryReserve: 2,
      proxyApiKeyFairShareCongestionThresholdPct: 80,
    });
  });

  it("renders inherited capacity values as empty inputs with effective hints", () => {
    render(
      <RoutingSettings
        settings={{
          ...BASE_SETTINGS,
          proxyAccountResponseCreateLimitOverride: null,
          proxyAccountStreamLimitOverride: null,
          proxyAccountStreamRecoveryReserveOverride: null,
          proxyApiKeyFairShareCongestionThresholdPctOverride: null,
        }}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByRole("spinbutton", { name: "Response-create limit" })).toHaveValue(null);
    expect(screen.getByRole("spinbutton", { name: "Stream limit" })).toHaveValue(null);
    expect(screen.getByRole("spinbutton", { name: "Stream recovery reserve" })).toHaveValue(null);
    expect(screen.getByRole("spinbutton", { name: "API key fair-share threshold (%)" })).toHaveValue(null);
    expect(screen.getAllByText(/Inherited effective value:/)).toHaveLength(4);
  });

  it("shows the legacy inherited hint only under empty inputs", () => {
    render(
      <RoutingSettings
        settings={{
          ...BASE_SETTINGS,
          proxyAccountResponseCreateLimitOverride: null,
          proxyAccountStreamLimitOverride: 24,
          proxyAccountStreamRecoveryReserveOverride: null,
          proxyApiKeyFairShareCongestionThresholdPctOverride: null,
        }}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByRole("spinbutton", { name: "Stream limit" })).toHaveValue(24);
    expect(screen.getAllByText(/Inherited effective value:/)).toHaveLength(3);
  });

  it("blocks resetting a stream limit whose inherited value the saved reserve would exceed", () => {
    render(
      <RoutingSettings
        settings={{
          ...BASE_SETTINGS,
          proxyAccountStreamLimit: 24,
          proxyAccountStreamLimitEnvironmentValue: 8,
          proxyAccountStreamLimitOverride: 24,
          proxyAccountStreamRecoveryReserve: 9,
          proxyAccountStreamRecoveryReserveEnvironmentValue: 1,
          proxyAccountStreamRecoveryReserveOverride: 9,
          provenance: {
            proxy_account_stream_limit: { source: "dashboard", envValue: 8, default: 8 },
            proxy_account_stream_recovery_reserve: { source: "dashboard", envValue: 1, default: 1 },
          },
        }}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    const resets = screen.getAllByRole("button", { name: "Reset to inherited" });
    expect(resets).toHaveLength(2);
    // Stream limit: clearing to 8 would leave the saved reserve of 9 above it.
    expect(resets[0]).toBeDisabled();
    expect(screen.getByText(/Reset is blocked: the stream recovery reserve would exceed/)).toBeInTheDocument();
    // Reserve: clearing to 1 stays below the saved stream limit of 24.
    expect(resets[1]).toBeEnabled();
  });

  it("shows provenance badges and resets a dashboard-owned cap to inherited", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const settings: DashboardSettings = {
      ...BASE_SETTINGS,
      proxyAccountResponseCreateLimitOverride: null,
      proxyAccountStreamLimitOverride: 24,
      proxyAccountStreamRecoveryReserveOverride: null,
      proxyApiKeyFairShareCongestionThresholdPctOverride: null,
      provenance: {
        proxy_account_response_create_limit: { source: "env", envValue: 6, default: 4 },
        proxy_account_stream_limit: { source: "dashboard", envValue: 8, default: 8 },
        proxy_account_stream_recovery_reserve: { source: "default", envValue: 1, default: 1 },
        proxy_api_key_fair_share_congestion_threshold_pct: { source: "default", envValue: 0, default: 0 },
      },
    };
    render(<RoutingSettings settings={settings} busy={false} onSave={onSave} />);

    expect(screen.getByText("Inherited from environment (6)")).toBeInTheDocument();
    expect(screen.getByText("Default (1)")).toBeInTheDocument();
    expect(screen.getByText("Default (0)")).toBeInTheDocument();
    expect(screen.queryByText(/Inherited effective value:/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Reset to inherited" }));

    expect(onSave).toHaveBeenCalledWith(buildSettingsUpdateRequest(settings, { proxyAccountStreamLimit: null }));
  });

  it("renders routing weights as inherited inputs and saves only the changed fields", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const settings: DashboardSettings = {
      ...BASE_SETTINGS,
      provenance: {
        proxy_overload_isolation_seconds: { source: "env", envValue: 600, default: 1800 },
        proxy_account_error_rate_weighting_enabled: { source: "default", envValue: true, default: true },
        proxy_account_inflight_penalty_pct: { source: "default", envValue: 2.5, default: 2.5 },
        proxy_account_lease_token_weight: { source: "default", envValue: 1, default: 1 },
        proxy_account_lease_ttl_seconds: { source: "default", envValue: 900, default: 900 },
      },
    };
    render(<RoutingSettings settings={settings} busy={false} onSave={onSave} />);

    expect(screen.getByRole("spinbutton", { name: "Overload isolation (seconds)" })).toHaveValue(null);
    expect(screen.getByRole("spinbutton", { name: "In-flight penalty (% per request)" })).toHaveValue(null);
    expect(screen.getByText("Inherited from environment (600)")).toBeInTheDocument();
    expect(screen.getByText("Default (2.5)")).toBeInTheDocument();
    expect(screen.getByText("Default (on)")).toBeInTheDocument();
    const saveButton = screen.getByRole("button", { name: "Save routing weights" });
    expect(saveButton).toBeDisabled();

    await user.type(screen.getByRole("spinbutton", { name: "Overload isolation (seconds)" }), "240");
    await user.type(screen.getByRole("spinbutton", { name: "In-flight penalty (% per request)" }), "7.5");
    await user.click(saveButton);

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      proxyOverloadIsolationSeconds: 240,
      proxyAccountInflightPenaltyPct: 7.5,
    });
    const payload = onSave.mock.calls[0]?.[0];
    expect(payload).not.toHaveProperty("proxyAccountLeaseTokenWeight");
    expect(payload).not.toHaveProperty("proxyAccountLeaseTtlSeconds");
  });

  it("clears a dashboard-owned routing weight with an explicit null and blocks out-of-bounds values", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const settings: DashboardSettings = {
      ...BASE_SETTINGS,
      proxyAccountLeaseTtlSeconds: 1200,
      provenance: {
        proxy_account_lease_ttl_seconds: { source: "dashboard", envValue: 900, default: 900 },
        proxy_account_inflight_penalty_pct: { source: "default", envValue: 2.5, default: 2.5 },
      },
    };
    render(<RoutingSettings settings={settings} busy={false} onSave={onSave} />);

    const ttlInput = screen.getByRole("spinbutton", { name: "Account lease TTL (seconds)" });
    expect(ttlInput).toHaveValue(1200);
    const saveButton = screen.getByRole("button", { name: "Save routing weights" });

    await user.type(screen.getByRole("spinbutton", { name: "In-flight penalty (% per request)" }), "150");
    expect(saveButton).toBeDisabled();
    await user.clear(screen.getByRole("spinbutton", { name: "In-flight penalty (% per request)" }));

    await user.clear(ttlInput);
    expect(saveButton).toBeEnabled();
    await user.click(saveButton);
    expect(onSave).toHaveBeenCalledWith({ ...BASE_UPDATE_PAYLOAD, proxyAccountLeaseTtlSeconds: null });

    await user.click(screen.getByRole("button", { name: "Reset to inherited" }));
    expect(onSave).toHaveBeenLastCalledWith(
      buildSettingsUpdateRequest(settings, { proxyAccountLeaseTtlSeconds: null }),
    );
  });

  it("toggles error-rate weighting as a dashboard value", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    const toggle = screen.getByRole("switch", { name: "Toggle error-rate weighting" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.click(toggle);

    expect(onSave).toHaveBeenCalledWith({ ...BASE_UPDATE_PAYLOAD, proxyAccountErrorRateWeightingEnabled: false });
  });

  it.each([
    ["Response-create limit", "proxyAccountResponseCreateLimit"],
    ["Stream limit", "proxyAccountStreamLimit"],
    ["Stream recovery reserve", "proxyAccountStreamRecoveryReserve"],
    ["API key fair-share threshold (%)", "proxyApiKeyFairShareCongestionThresholdPct"],
  ] as const)("sends only an explicit null when clearing %s", async (label, field) => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    await user.clear(screen.getByRole("spinbutton", { name: label }));
    await user.click(screen.getByRole("button", { name: "Save capacity limits" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      [field]: null,
    });
  });

  it("does not pin unedited capacity values when clearing an inherited field", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const inheritedSettings = {
      ...BASE_SETTINGS,
      proxyAccountResponseCreateLimitOverride: null,
      proxyAccountStreamLimitOverride: null,
      proxyAccountStreamRecoveryReserveOverride: null,
      proxyApiKeyFairShareCongestionThresholdPctOverride: null,
    };
    render(<RoutingSettings settings={inheritedSettings} busy={false} onSave={onSave} />);

    await user.type(screen.getByRole("spinbutton", { name: "Stream limit" }), "12");
    await user.click(screen.getByRole("button", { name: "Save capacity limits" }));

    expect(onSave).toHaveBeenCalledWith({
      ...buildSettingsUpdateRequest(inheritedSettings, {}),
      proxyAccountStreamLimit: 12,
    });
    const payload = onSave.mock.calls[0]?.[0];
    expect(payload).not.toHaveProperty("proxyAccountResponseCreateLimit");
    expect(payload).not.toHaveProperty("proxyAccountStreamRecoveryReserve");
    expect(payload).not.toHaveProperty("proxyApiKeyFairShareCongestionThresholdPct");
  });

  it("validates a cleared stream limit against its inherited environment value", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings
        settings={{
          ...BASE_SETTINGS,
          proxyAccountStreamLimit: 24,
          proxyAccountStreamLimitEnvironmentValue: 2,
          proxyAccountStreamLimitOverride: 24,
          proxyAccountStreamRecoveryReserve: 3,
          proxyAccountStreamRecoveryReserveOverride: 3,
        }}
        busy={false}
        onSave={onSave}
      />,
    );

    await user.clear(screen.getByRole("spinbutton", { name: "Stream limit" }));

    expect(screen.getByRole("button", { name: "Save capacity limits" })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("rejects invalid account capacity limits before saving", async () => {
    const user = userEvent.setup();
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    const streamLimit = screen.getByRole("spinbutton", { name: "Stream limit" });
    const recoveryReserve = screen.getByRole("spinbutton", { name: "Stream recovery reserve" });
    const fairShareThreshold = screen.getByRole("spinbutton", {
      name: "API key fair-share threshold (%)",
    });
    const saveButton = screen.getByRole("button", { name: "Save capacity limits" });

    await user.clear(streamLimit);
    await user.type(streamLimit, "2");
    await user.clear(recoveryReserve);
    await user.type(recoveryReserve, "3");
    expect(saveButton).toBeDisabled();

    await user.clear(recoveryReserve);
    await user.type(recoveryReserve, "1.5");
    expect(saveButton).toBeDisabled();

    await user.clear(recoveryReserve);
    await user.type(recoveryReserve, "1");
    expect(saveButton).toBeEnabled();

    await user.clear(fairShareThreshold);
    await user.type(fairShareThreshold, "101");
    expect(saveButton).toBeDisabled();
  });

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

  it("saves the Fast Mode prohibition toggle", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    await user.click(screen.getByRole("switch", { name: "Prohibit Fast Mode" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      prohibitFastMode: true,
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
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
      warmupModel: "gpt-5.4-pro",
      }),
    );
  });

  it("shows the configured upstream transport", () => {
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    expect(screen.getByText("Upstream stream transport")).toBeInTheDocument();
    expect(screen.getByText("Auto")).toBeInTheDocument();
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

    await user.click(screen.getByRole("button", { name: /Single account/i }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      routingStrategy: "single_account",
      singleAccountId: "acc-one",
    });
  });

  it("names limit warm-up controls for assistive technology", () => {
    render(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, limitWarmupEnabled: true }}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByRole("switch", { name: "Enable limit warm-up" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Enable staggered idle warm-up" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Prefer earlier reset accounts" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Reset preference window" })).toBeInTheDocument();
    expect(screen.getByLabelText("Warm-up model")).toHaveAttribute("maxLength", "128");
    expect(screen.getByRole("combobox", { name: "Warm-up windows" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Pace gap average" })).toBeInTheDocument();
    expect(screen.getByLabelText("Model")).toHaveAttribute("maxLength", "128");
    expect(screen.getByLabelText("Min usage percent")).toHaveAttribute("max", "100");
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

  it("saves weekly pace smoothing changes", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={onSave} />);

    await user.click(screen.getByRole("combobox", { name: "Pace gap average" }));
    await user.click(await screen.findByRole("option", { name: "2h" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      weeklyPaceSmoothingMinutes: 120,
    });
  });

  it("does not silently truncate decimal warm-up cooldown values", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, limitWarmupEnabled: true }}
        busy={false}
        onSave={onSave}
      />,
    );

    await user.clear(screen.getByLabelText("Warm-up cooldown"));
    await user.type(screen.getByLabelText("Warm-up cooldown"), "60.5");

    expect(screen.getByRole("button", { name: "Save warm-up settings" })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("saves warm-up exhausted threshold changes", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, limitWarmupEnabled: true }}
        busy={false}
        onSave={onSave}
      />,
    );

    await user.clear(screen.getByLabelText("Min usage percent"));
    await user.type(screen.getByLabelText("Min usage percent"), "98.5");
    await user.click(screen.getByRole("button", { name: "Save warm-up settings" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      limitWarmupEnabled: true,
      limitWarmupExhaustedThresholdPercent: 98.5,
    });
  });

  it("rejects invalid warm-up exhausted thresholds", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, limitWarmupEnabled: true }}
        busy={false}
        onSave={onSave}
      />,
    );

    await user.clear(screen.getByLabelText("Min usage percent"));
    await user.type(screen.getByLabelText("Min usage percent"), "100.1");

    expect(screen.getByRole("button", { name: "Save warm-up settings" })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("saves staggered idle warm-up idle threshold changes", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <RoutingSettings
        settings={{ ...BASE_SETTINGS, limitWarmupEnabled: true, limitWarmupStaggeredIdleEnabled: true }}
        busy={false}
        onSave={onSave}
      />,
    );

    await user.clear(screen.getByLabelText("Max usage percent"));
    await user.type(screen.getByLabelText("Max usage percent"), "2.5");
    await user.click(screen.getByRole("button", { name: "Save warm-up settings" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      limitWarmupEnabled: true,
      limitWarmupStaggeredIdleEnabled: true,
      limitWarmupIdleThresholdPercent: 2.5,
    });
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

  it("explains routing strategy trade-offs and account-safety guidance", () => {
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    expect(screen.getByText(/Good default for compliant mixed-account pools/i)).toBeInTheDocument();
    expect(screen.getByText(/No strategy can guarantee account-safety outcomes/i)).toBeInTheDocument();
  });

  it("explains soft sticky routing versus hard Codex continuation affinity", () => {
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    expect(
      screen.getByText(/does not disable hard Codex continuation affinity/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/soft preference, not a guarantee/i)).toBeInTheDocument();
  });

  it("explains primary versus secondary quota windows and threshold units", () => {
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    expect(screen.getByText("Primary vs secondary quota")).toBeInTheDocument();
    expect(
      screen.getByText(/Primary quota is the short 5-hour usage window/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/5-hour \(primary\) window has been used/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/secondary window \(weekly, or monthly on monthly-only plans\) has been used/i),
    ).toBeInTheDocument();
  });

  it("shows the remaining-percent equivalent for sticky thresholds", async () => {
    const user = userEvent.setup();
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    // Defaults: primary 95% used, secondary 100% used.
    expect(screen.getByText("95% used · 5% remaining in quota terms")).toBeInTheDocument();
    expect(screen.getByText("100% used · 0% remaining in quota terms")).toBeInTheDocument();

    const secondary = screen.getByRole("spinbutton", { name: "Sticky secondary threshold" });
    await user.clear(secondary);
    await user.type(secondary, "70");

    expect(screen.getByText("70% used · 30% remaining in quota terms")).toBeInTheDocument();

    // Decimal thresholds keep the two displayed values complementary.
    await user.clear(secondary);
    await user.type(secondary, "12.5");

    expect(screen.getByText("12.5% used · 87.5% remaining in quota terms")).toBeInTheDocument();
  });

  it("describes prefer-earlier-reset selection behavior", () => {
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    expect(
      screen.getByText(/prefer those whose selected quota window resets sooner/i),
    ).toBeInTheDocument();
  });

  it("describes what limit warm-up sends and that probes consume quota", () => {
    render(<RoutingSettings settings={BASE_SETTINGS} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} />);

    expect(screen.getByText(/consume a small amount of quota/i)).toBeInTheDocument();
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

describe("RoutingSettings subscription overflow", () => {
  it("renders the overflow designation inside the routing card", () => {
    render(
      <RoutingSettings
        settings={BASE_SETTINGS}
        modelSources={[createModelSource({ id: "src_responses", name: "Responses source", supportsResponses: true })]}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(
      screen.getByRole("combobox", { name: "Overflow to model source when all subscription accounts are exhausted" }),
    ).toHaveTextContent("Off");
  });
});
