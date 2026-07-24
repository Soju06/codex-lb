import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";

import { ConversationsView } from "@/features/dashboard/components/conversations-view";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

describe("ConversationsView", () => {
  it("renders the list without a conversation filter", async () => {
    window.history.pushState({}, "", "/dashboard?view=conversations");
    renderWithProviders(<ConversationsView accounts={[]} />);

    expect(await screen.findByText("conv_abc")).toBeInTheDocument();
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/timeframe/i)).not.toBeInTheDocument();
  });

  it("renders the established empty state", async () => {
    window.history.pushState({}, "", "/dashboard?view=conversations&conversationSearch=missing");
    renderWithProviders(<ConversationsView />);

    expect(await screen.findByText("No conversations yet")).toBeInTheDocument();
  });

  it("shows the error and Retry action when a refetch fails with stale data", async () => {
    const result = renderWithProviders(<ConversationsView />);
    expect(await screen.findByText("conv_abc")).toBeInTheDocument();

    server.use(
      http.get("/api/conversations", () =>
        HttpResponse.json({ error: { message: "Conversation list unavailable" } }, { status: 503 }),
      ),
    );
    await result.queryClient.refetchQueries({ queryKey: ["dashboard", "conversations"] });

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Conversation list unavailable");
    });
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByText("conv_abc")).toBeInTheDocument();
  });
});
