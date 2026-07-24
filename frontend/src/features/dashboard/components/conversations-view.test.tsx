import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";

import { ConversationsView } from "@/features/dashboard/components/conversations-view";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

describe("ConversationsView", () => {
  it("has one search input, no timeframe controls, and preserves the list state", async () => {
    window.history.pushState({}, "", "/dashboard?view=conversations");
    renderWithProviders(<ConversationsView />);

    expect(await screen.findByText("conv_abc")).toBeInTheDocument();
    const search = screen.getByRole("searchbox");
    expect(screen.getAllByRole("searchbox")).toHaveLength(1);
    expect(search).toHaveAttribute("name", "conversationSearch");
    expect(search).toHaveAttribute("autocomplete", "off");
    expect(search).toHaveAccessibleName("Search conversations");
    expect(screen.queryByText(/timeframe/i)).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.type(screen.getByRole("searchbox"), "opencode");
    expect(window.location.search).toContain("conversationSearch=opencode");
    expect(window.location.search).toContain("conversationOffset=0");
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
