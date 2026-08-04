import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";

import { ConversationDetailsDialog } from "@/features/dashboard/components/conversation-details-dialog";
import { createConversationDetails } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

describe("ConversationDetailsDialog", () => {
  it("keeps null and empty reasoning efforts as distinct rendered rows", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    server.use(
      http.get("/api/conversations/conv_distinct_efforts", () =>
        HttpResponse.json(
          createConversationDetails({
            conversationId: "conv_distinct_efforts",
            modelStats: [
              {
                ...createConversationDetails().modelStats[0],
                modelEffort: { model: "gpt-5.1", reasoningEffort: null },
              },
              {
                ...createConversationDetails().modelStats[0],
                modelEffort: { model: "gpt-5.1", reasoningEffort: "" },
                reqs: 3,
              },
            ],
          }),
        ),
      ),
    );

    try {
      renderWithProviders(
        <ConversationDetailsDialog
          open
          conversationId="conv_distinct_efforts"
          onOpenChange={() => {}}
        />,
      );

      expect(await screen.findAllByRole("row")).toHaveLength(3);
      expect(consoleError.mock.calls.flat().join(" ")).not.toMatch(/same key/i);
    } finally {
      consoleError.mockRestore();
    }
  });

  it("keeps metadata and the detail table in one bordered information box", async () => {
    server.use(
      http.get("/api/conversations/conv_layout", () =>
        HttpResponse.json(createConversationDetails({ conversationId: "conv_layout" })),
      ),
    );

    renderWithProviders(
      <ConversationDetailsDialog open conversationId="conv_layout" onOpenChange={() => {}} />,
    );

    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.getByTestId("conversation-details-information")).toContainElement(
      screen.getByRole("table"),
    );
  });

  it("sorts every displayed column in both directions while preserving ties", async () => {
    server.use(
      http.get("/api/conversations/conv_sort_all", () =>
        HttpResponse.json(
          createConversationDetails({
            conversationId: "conv_sort_all",
            modelStats: [
              {
                modelEffort: { model: "gpt-z", reasoningEffort: "high" },
                reqs: 3,
                totalElapsedTime: 300,
                totalInputTokens: 30,
                cachedInputTokens: 3,
                totalOutputTokens: 300,
                totalCostUsd: 0.3,
              },
              {
                modelEffort: { model: "gpt-a", reasoningEffort: "low" },
                reqs: 3,
                totalElapsedTime: 200,
                totalInputTokens: 20,
                cachedInputTokens: 2,
                totalOutputTokens: 200,
                totalCostUsd: 0.2,
              },
              {
                modelEffort: { model: "gpt-m", reasoningEffort: "medium" },
                reqs: 1,
                totalElapsedTime: 100,
                totalInputTokens: 10,
                cachedInputTokens: 1,
                totalOutputTokens: 100,
                totalCostUsd: 0.1,
              },
            ],
          }),
        ),
      ),
    );

    renderWithProviders(
      <ConversationDetailsDialog open conversationId="conv_sort_all" onOpenChange={() => {}} />,
    );
    expect(await screen.findByRole("table")).toBeInTheDocument();

    const user = userEvent.setup();
    const modelOrder = () =>
      screen
        .getAllByRole("row")
        .slice(1)
        .map((row) => within(row).getAllByRole("cell")[0]?.textContent?.trim());
    const sortBothDirections = async (
      column: string,
      descending: string[],
      ascending: string[],
    ) => {
      await user.click(screen.getByRole("button", { name: column }));
      expect(modelOrder()).toEqual(descending);
      await user.click(screen.getByRole("button", { name: column }));
      expect(modelOrder()).toEqual(ascending);
    };

    await sortBothDirections("Model (effort)", ["gpt-z (high)", "gpt-m (medium)", "gpt-a (low)"], ["gpt-a (low)", "gpt-m (medium)", "gpt-z (high)"]);
    await sortBothDirections("Reqs", ["gpt-z (high)", "gpt-a (low)", "gpt-m (medium)"], ["gpt-m (medium)", "gpt-z (high)", "gpt-a (low)"]);
    await sortBothDirections("Total elapsed", ["gpt-z (high)", "gpt-a (low)", "gpt-m (medium)"], ["gpt-m (medium)", "gpt-a (low)", "gpt-z (high)"]);
    await sortBothDirections("Total input", ["gpt-z (high)", "gpt-a (low)", "gpt-m (medium)"], ["gpt-m (medium)", "gpt-a (low)", "gpt-z (high)"]);
    await sortBothDirections("Total output", ["gpt-z (high)", "gpt-a (low)", "gpt-m (medium)"], ["gpt-m (medium)", "gpt-a (low)", "gpt-z (high)"]);
    await sortBothDirections("Total cost", ["gpt-z (high)", "gpt-a (low)", "gpt-m (medium)"], ["gpt-m (medium)", "gpt-a (low)", "gpt-z (high)"]);
  });

  it("loads lazily, shows the request-detail shell, and sorts returned rows client-side", async () => {
    let calls = 0;
    server.use(
      http.get("/api/conversations/conv%20encoded", () => {
        calls += 1;
        return HttpResponse.json(
          createConversationDetails({
            conversationId: "conv encoded",
            modelStats: [
              {
                ...createConversationDetails().modelStats[0],
                modelEffort: { model: "gpt-5.4-mini", reasoningEffort: "high" },
                reqs: 2,
              },
              {
                ...createConversationDetails().modelStats[1],
                modelEffort: { model: "gpt-5.1", reasoningEffort: null },
                reqs: 8,
              },
            ],
          }),
        );
      }),
    );

    renderWithProviders(
      <ConversationDetailsDialog
        open
        conversationId="conv encoded"
        onOpenChange={() => {}}
      />,
    );

    expect(calls).toBe(0);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(await screen.findByText("conv encoded")).toBeInTheDocument();
    expect(screen.getByText("Dominant user-agent")).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent?.replace(/[↑↓]/g, ""))).toEqual([
      "Model (effort)",
      "Reqs",
      "Total elapsed",
      "Total input",
      "Total output",
      "Total cost",
    ]);

    const rows = screen.getAllByRole("row");
    expect(rows[1]).toHaveTextContent("gpt-5.1");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /total cost/i }));
    expect(screen.getAllByRole("row")[1]).toHaveTextContent("gpt-5.4-mini (high)");
  });

  it("shows the conversation ID without a copy action", async () => {
    server.use(
      http.get("/api/conversations/conv_no_copy", () =>
        HttpResponse.json(createConversationDetails({ conversationId: "conv_no_copy" })),
      ),
    );

    renderWithProviders(
      <ConversationDetailsDialog open conversationId="conv_no_copy" onOpenChange={() => {}} />,
    );

    expect(await screen.findByText("conv_no_copy")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /copy conversation id/i })).not.toBeInTheDocument();
  });

  it("uses the standard retry surface for a detail error", async () => {
    server.use(
      http.get("/api/conversations/missing", () =>
        HttpResponse.json({ error: { message: "Conversation not found" } }, { status: 404 }),
      ),
    );

    renderWithProviders(
      <ConversationDetailsDialog
        open
        conversationId="missing"
        onOpenChange={() => {}}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Conversation not found");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("uses the standard error surface for a malformed blank ID", async () => {
    renderWithProviders(
      <ConversationDetailsDialog
        open
        conversationId="   "
        onOpenChange={() => {}}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Conversation not found");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("uses the em-dash fallback for a null cached detail total", async () => {
    server.use(
      http.get("/api/conversations/conv_null_cache", () => {
        const details = createConversationDetails({ conversationId: "conv_null_cache" });
        return HttpResponse.json({
          ...details,
          modelStats: details.modelStats.map((stat) => ({
            ...stat,
            cachedInputTokens: null,
          })),
        });
      }),
    );

    renderWithProviders(
      <ConversationDetailsDialog
        open
        conversationId="conv_null_cache"
        onOpenChange={() => {}}
      />,
    );

    expect((await screen.findAllByText("(cache —)")).length).toBeGreaterThan(0);
    expect(screen.queryByText(/\(cache --\)/i)).not.toBeInTheDocument();
  });
});
