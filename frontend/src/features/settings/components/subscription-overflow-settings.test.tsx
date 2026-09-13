import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { SubscriptionOverflowSettings } from "@/features/settings/components/subscription-overflow-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings } from "@/features/settings/schemas";
import {
  createDashboardSettings,
  createModelSource,
  createSubscriptionOverflowPreflight,
} from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";
import { formatDateTimeInline } from "@/utils/formatters";

const DAY_MS = 86_400_000;
const OVERFLOW_LABEL = "Overflow to model source when all subscription accounts are exhausted";
const BASE_SETTINGS: DashboardSettings = createDashboardSettings();
const BASE_UPDATE_PAYLOAD = buildSettingsUpdateRequest(BASE_SETTINGS, {});
const SOURCES = [
  createModelSource({ id: "src_responses", name: "Responses source", supportsResponses: true }),
  createModelSource({ id: "src_chat", name: "Chat only", supportsResponses: false }),
];

function renderOverflow(overrides: Partial<DashboardSettings> = {}, onSave = vi.fn().mockResolvedValue(undefined)) {
  const settings = { ...BASE_SETTINGS, ...overrides };
  renderWithProviders(
    <SubscriptionOverflowSettings settings={settings} modelSources={SOURCES} busy={false} onSave={onSave} />,
  );
  return { settings, onSave };
}

describe("SubscriptionOverflowSettings", () => {
  it("renders Off when no source is designated and lists only Responses-capable sources", async () => {
    const user = userEvent.setup();
    renderOverflow();

    const trigger = screen.getByRole("combobox", { name: OVERFLOW_LABEL });
    expect(trigger).toHaveTextContent("Off");
    expect(screen.queryByTestId("subscription-overflow-preflight")).not.toBeInTheDocument();

    await user.click(trigger);
    expect(screen.getByRole("option", { name: "Off" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Responses source" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Chat only/ })).not.toBeInTheDocument();
  });

  it("designates a source through the full settings payload", async () => {
    const user = userEvent.setup();
    const { onSave } = renderOverflow();

    await user.click(screen.getByRole("combobox", { name: OVERFLOW_LABEL }));
    await user.click(screen.getByRole("option", { name: "Responses source" }));

    expect(onSave).toHaveBeenCalledWith({
      ...BASE_UPDATE_PAYLOAD,
      subscriptionOverflowSourceId: "src_responses",
    });
  });

  it("turns overflow off with an explicit null", async () => {
    const user = userEvent.setup();
    const { onSave, settings } = renderOverflow({ subscriptionOverflowSourceId: "src_responses" });

    expect(screen.getByRole("combobox", { name: OVERFLOW_LABEL })).toHaveTextContent("Responses source");
    await user.click(screen.getByRole("combobox", { name: OVERFLOW_LABEL }));
    await user.click(screen.getByRole("option", { name: "Off" }));

    expect(onSave).toHaveBeenCalledWith({
      ...buildSettingsUpdateRequest(settings, {}),
      subscriptionOverflowSourceId: null,
    });
  });

  it("keeps an ineligible designated source visible but unselectable", async () => {
    const user = userEvent.setup();
    renderOverflow({ subscriptionOverflowSourceId: "src_chat" });

    await user.click(screen.getByRole("combobox", { name: OVERFLOW_LABEL }));
    const blocked = screen.getByRole("option", { name: "Chat only (no longer eligible)" });
    expect(blocked).toHaveAttribute("aria-disabled", "true");
  });

  it("shows the drain notice with the date pinned conversations expire, not the 29-day deadline", () => {
    // Cleared two days ago: pins expire at clear + 7 d, the lookup window closes at clear + 29 d.
    const pinsExpireBy = new Date(Date.now() + 5 * DAY_MS).toISOString();
    const drainUntil = new Date(Date.now() + 27 * DAY_MS).toISOString();
    renderOverflow({ subscriptionOverflowDrainUntil: drainUntil, subscriptionOverflowPinsExpireBy: pinsExpireBy });

    const notice = screen.getByText(/Overflow is off\./);
    expect(notice).toHaveTextContent(`keep working until ${formatDateTimeInline(pinsExpireBy)} at the latest`);
    expect(notice).not.toHaveTextContent(formatDateTimeInline(drainUntil));
  });

  it("hides the drain notice once every pinned conversation has expired, even while the deadline is armed", () => {
    // Cleared nine days ago: no pin can still be live, although lookups run for 20 more days.
    renderOverflow({
      subscriptionOverflowDrainUntil: new Date(Date.now() + 20 * DAY_MS).toISOString(),
      subscriptionOverflowPinsExpireBy: new Date(Date.now() - 2 * DAY_MS).toISOString(),
    });
    expect(screen.queryByText(/Overflow is off\./)).not.toBeInTheDocument();
  });

  it("never shows the drain notice while a source is designated", () => {
    renderOverflow({
      subscriptionOverflowSourceId: "src_responses",
      subscriptionOverflowPinsExpireBy: new Date(Date.now() + 5 * DAY_MS).toISOString(),
    });
    expect(screen.queryByText(/Overflow is off\./)).not.toBeInTheDocument();
  });

  it("asks for a Responses-capable source when none exists", () => {
    renderWithProviders(
      <SubscriptionOverflowSettings
        settings={BASE_SETTINGS}
        modelSources={[SOURCES[1]]}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(screen.getByText(/Add an OpenAI-compatible model source with Responses support/)).toBeInTheDocument();
  });

  it("reports a failed model-source load instead of asking for a source", () => {
    renderWithProviders(
      <SubscriptionOverflowSettings
        settings={BASE_SETTINGS}
        modelSources={[]}
        modelSourcesError
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Model sources could not be loaded");
    expect(
      screen.queryByText(/Add an OpenAI-compatible model source with Responses support/),
    ).not.toBeInTheDocument();
  });

  it("does not mark the designated source deleted while the source list failed to load", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/settings/subscription-overflow/preflight", () =>
        HttpResponse.json(createSubscriptionOverflowPreflight({ sourceId: "src_responses" })),
      ),
    );
    renderWithProviders(
      <SubscriptionOverflowSettings
        settings={{ ...BASE_SETTINGS, subscriptionOverflowSourceId: "src_responses" }}
        modelSources={[]}
        modelSourcesError
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    const trigger = screen.getByRole("combobox", { name: OVERFLOW_LABEL });
    expect(trigger).toHaveTextContent("src_responses");
    await user.click(trigger);
    expect(screen.queryByRole("option", { name: /\(deleted\)/ })).not.toBeInTheDocument();
  });

  it("renders the preflight report for the designated source", async () => {
    server.use(
      http.get("/api/settings/subscription-overflow/preflight", ({ request }) => {
        const sourceId = new URL(request.url).searchParams.get("source_id");
        return HttpResponse.json(
          createSubscriptionOverflowPreflight({
            sourceId: sourceId ?? "",
            sourceName: "Responses source",
            sourceEnabled: false,
            blockers: ["source_responses_unsupported"],
            eligible: false,
          }),
        );
      }),
    );
    renderOverflow({ subscriptionOverflowSourceId: "src_responses" });

    await waitFor(() => expect(screen.getByTestId("subscription-overflow-preflight")).toBeInTheDocument());
    expect(screen.getByText("Source does not support the Responses API")).toBeInTheDocument();
    expect(screen.getByText("Source disabled")).toBeInTheDocument();
    expect(screen.getByText("Not served by this source: gpt-5.5")).toBeInTheDocument();
    expect(screen.getByText("Undeclared tool types: shell, tool_search")).toBeInTheDocument();
    expect(screen.getByText("No vision")).toBeInTheDocument();
    expect(screen.getByText("No pricing — cost tile shows $0")).toBeInTheDocument();
    expect(
      screen.getByText("Context window 8192 is smaller than the registry's 272000 — Codex compaction will fail every turn"),
    ).toBeInTheDocument();
    expect(screen.getByText("Cannot overflow in this version (Responses-Lite / code mode)")).toBeInTheDocument();
    expect(screen.getByText("1 API key(s) are scoped to this source (they never overflow)")).toBeInTheDocument();
    expect(screen.getByText("2 live pinned conversations · 1 expired")).toBeInTheDocument();
  });

  it("reports an unavailable preflight instead of hiding the designation", async () => {
    server.use(
      http.get("/api/settings/subscription-overflow/preflight", () =>
        HttpResponse.json({ error: { code: "not_found", message: "Model source not found" } }, { status: 404 }),
      ),
    );
    renderOverflow({ subscriptionOverflowSourceId: "src_responses" });

    expect(await screen.findByRole("alert")).toHaveTextContent("Preflight is unavailable right now.");
    expect(screen.getByRole("combobox", { name: OVERFLOW_LABEL })).toHaveTextContent("Responses source");
  });

  it("expands the help text on demand", async () => {
    const user = userEvent.setup();
    renderOverflow();

    expect(screen.queryByText(/Triggers only on pool-wide usage exhaustion/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "How overflow works" }));
    expect(screen.getByText(/Triggers only on pool-wide usage exhaustion/)).toBeInTheDocument();
    expect(screen.getByText(/gpt-5.6 conversations cannot overflow in this version/)).toBeInTheDocument();
    expect(screen.getByText(/exceeded retry limit, last status: 429/)).toBeInTheDocument();
  });
});
