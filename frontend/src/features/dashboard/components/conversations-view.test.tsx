import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type PropsWithChildren } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ConversationsView } from "@/features/dashboard/components/conversations-view";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });
}

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

  it("standalone mount sends timeframe without browser-generated since", async () => {
    const apiRequests: URL[] = [];
    server.use(
      http.get("/api/conversations", ({ request }) => {
        apiRequests.push(new URL(request.url));
        return HttpResponse.json({ conversations: [], total: 0, hasMore: false });
      }),
    );

    const queryClient = createTestQueryClient();
    const Wrapper = ({ children }: PropsWithChildren) =>
      createElement(
        QueryClientProvider,
        { client: queryClient },
        createElement(
          MemoryRouter,
          { initialEntries: ["/dashboard?view=conversations&conversationTimeframe=7d"] },
          children,
        ),
      );

    render(<ConversationsView accounts={[]} />, { wrapper: Wrapper });

    await waitFor(() => expect(apiRequests).toHaveLength(1));
    expect(apiRequests[0]?.searchParams.get("timeframe")).toBe("7d");
    expect(apiRequests[0]?.searchParams.has("since")).toBe(false);
  });
});
