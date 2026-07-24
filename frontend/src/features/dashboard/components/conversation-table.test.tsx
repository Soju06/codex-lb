import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { createAccountSummary, createConversationEntry } from "@/test/mocks/factories";
import { ConversationTable } from "@/features/dashboard/components/conversation-table";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { formatTimeLong } from "@/utils/formatters";

describe("ConversationTable", () => {
  beforeEach(() => {
    usePrivacyStore.setState({ blurred: false });
  });

  it("blurs only email-derived account fallback labels", () => {
    usePrivacyStore.setState({ blurred: true });

    render(
      <ConversationTable
        conversations={[
          createConversationEntry({ conversationId: "conv_display", representativeAccount: "acc-display" }),
          createConversationEntry({ conversationId: "conv_email", representativeAccount: "acc-email" }),
          createConversationEntry({ conversationId: "conv_unknown", representativeAccount: "acc-unknown" }),
        ]}
        accounts={[
          createAccountSummary({
            accountId: "acc-display",
            displayName: "Named Account",
            email: "named@example.com",
          }),
          createAccountSummary({
            accountId: "acc-email",
            displayName: "",
            email: "fallback@example.com",
          }),
        ]}
        total={3}
        limit={25}
        offset={0}
        hasMore={false}
        onLimitChange={vi.fn()}
        onOffsetChange={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    const blurredEmail = screen.getByText("fallback@example.com");
    expect(blurredEmail).toHaveClass("privacy-blur");
    expect(blurredEmail.closest("[title]")).toBeNull();
    expect(screen.getByText("Named Account")).not.toHaveClass("privacy-blur");
    expect(screen.getByText("acc-unknown")).not.toHaveClass("privacy-blur");
  });

  it("renders the exact aggregate columns and subordinate values", () => {
    render(
      <ConversationTable
        conversations={[
          createConversationEntry({
            conversationId: "conv_visible",
            representativeAccount: "acc-1",
            lastRequest: "2026-01-01T12:00:00.000Z",
            remainingAccountCount: 2,
            apiKeyName: "Operator key",
            representativeModel: "gpt-5.4",
            remainingModelCount: 1,
            totalTokens: 2048,
            cachedInputTokens: 512,
            totalCostUsd: 0.42,
          }),
        ]}
        accounts={[
          createAccountSummary({
            accountId: "acc-1",
            displayName: "Primary Account",
            email: "owner@example.com",
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
      "Last request",
      "Conversation",
      "Accounts",
      "API key",
      "Models",
      "Tokens",
      "Cost",
      "Details",
    ]);
    expect(screen.getByText("conv_visible")).toBeInTheDocument();
    expect(screen.getByText("conv_visible")).toHaveAttribute("translate", "no");
    expect(screen.getByText("Primary Account")).toBeInTheDocument();
    expect(screen.queryByText("acc-1")).not.toBeInTheDocument();
    const time = formatTimeLong("2026-01-01T12:00:00.000Z");
    expect(screen.getByText(time.time)).toBeInTheDocument();
    expect(screen.getByText(time.date)).toBeInTheDocument();
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
        accounts={[]}
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
        accounts={[]}
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
        accounts={[]}
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

  it("falls back to the account email when display name is empty", () => {
    render(
      <ConversationTable
        conversations={[createConversationEntry({ representativeAccount: "acc-email" })]}
        accounts={[
          createAccountSummary({
            accountId: "acc-email",
            displayName: "",
            email: "fallback@example.com",
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

    expect(screen.getByText("fallback@example.com")).toBeInTheDocument();
    expect(screen.queryByText("acc-email")).not.toBeInTheDocument();
  });

  it("falls back to the account ID when the account is unknown", () => {
    render(
      <ConversationTable
        conversations={[createConversationEntry({ representativeAccount: "acc-unknown" })]}
        accounts={[]}
        total={1}
        limit={25}
        offset={0}
        hasMore={false}
        onLimitChange={vi.fn()}
        onOffsetChange={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getByText("acc-unknown")).toBeInTheDocument();
  });
});
