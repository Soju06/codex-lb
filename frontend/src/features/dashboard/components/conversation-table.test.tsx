import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { createConversationEntry } from "@/test/mocks/factories";
import { ConversationTable } from "@/features/dashboard/components/conversation-table";

describe("ConversationTable", () => {
  it("renders the exact aggregate columns and subordinate values", () => {
    render(
      <ConversationTable
        conversations={[
          createConversationEntry({
            conversationId: "conv_visible",
            representativeAccount: "Account one",
            remainingAccountCount: 2,
            apiKeyName: "Operator key",
            representativeModel: "gpt-5.4",
            remainingModelCount: 1,
            totalTokens: 2048,
            cachedInputTokens: 512,
            totalCostUsd: 0.42,
          }),
        ]}
        total={1}
        limit={25}
        offset={0}
        hasMore={false}
        onLimitChange={vi.fn()}
        onOffsetChange={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    const headers = screen.getAllByRole("columnheader").map((header) => header.textContent);
    expect(headers).toEqual([
      "Conversation",
      "Last request",
      "Accounts",
      "API key",
      "Models",
      "Tokens",
      "Cost",
      "Details",
    ]);
    expect(screen.getByText("conv_visible")).toBeInTheDocument();
    expect(screen.getByText("conv_visible")).toHaveAttribute("translate", "no");
    expect(screen.getByText("+ 2 more")).toBeInTheDocument();
    expect(screen.getByText("+ 1 more")).toBeInTheDocument();
    expect(screen.getByText(/cached/i)).toBeInTheDocument();
    expect(screen.getByText("Operator key")).toBeInTheDocument();
    expect(screen.getByTitle("gpt-5.4").querySelector('[translate="no"]')).toHaveTextContent("gpt-5.4");
    expect(screen.queryByText("key_1")).not.toBeInTheDocument();

    const detailsButton = screen.getByRole("button", { name: /details/i });
    expect(detailsButton).toBeInTheDocument();
    expect(within(detailsButton).queryByText("key_1")).not.toBeInTheDocument();
  });

  it("uses dashboard fallbacks for nullable list values", () => {
    render(
      <ConversationTable
        conversations={[
          createConversationEntry({
            conversationId: "conv_nullable",
            representativeAccount: null,
            apiKeyName: null,
            representativeModel: null,
          }),
        ]}
        total={1}
        limit={25}
        offset={0}
        hasMore={false}
        onLimitChange={vi.fn()}
        onOffsetChange={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
  });

  it("uses the em-dash fallback for a null cached total", () => {
    render(
      <ConversationTable
        conversations={[createConversationEntry({ cachedInputTokens: null as unknown as number })]}
        total={1}
        limit={25}
        offset={0}
        hasMore={false}
        onLimitChange={vi.fn()}
        onOffsetChange={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getByText(/— cached/)).toBeInTheDocument();
    expect(screen.queryByText(/-- cached|cached --/i)).not.toBeInTheDocument();
  });

  it("gives each details action a conversation-specific accessible name", () => {
    render(
      <ConversationTable
        conversations={[createConversationEntry({ conversationId: "conv_named" })]}
        total={1}
        limit={25}
        offset={0}
        hasMore={false}
        onLimitChange={vi.fn()}
        onOffsetChange={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /details.*conv_named/i })).toBeInTheDocument();
  });
});
