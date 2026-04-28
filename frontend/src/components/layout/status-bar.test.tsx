import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBar } from "@/components/layout/status-bar";

function renderStatusBar() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <StatusBar />
    </QueryClientProvider>,
  );
}

describe("StatusBar", () => {
  it("links to the official GitHub repository", () => {
    renderStatusBar();

    const link = screen.getByRole("link", { name: "Open official GitHub repository" });

    expect(link).toHaveAttribute("href", "https://github.com/soju06/codex-lb");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });
});
