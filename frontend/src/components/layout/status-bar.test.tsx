import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { StatusBar } from "@/components/layout/status-bar";
import { server } from "@/test/mocks/server";

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

  it("links to release notes when a newer version is available", async () => {
    server.use(
      http.get("/api/runtime/version", () =>
        HttpResponse.json({
          currentVersion: "1.19.0",
          latestVersion: "1.20.0",
          updateAvailable: true,
          checkedAt: "2026-05-26T00:00:00Z",
          source: "github",
          releaseUrl: "https://github.com/Soju06/codex-lb/releases/latest",
        }),
      ),
    );

    renderStatusBar();

    const link = await screen.findByRole("link", {
      name: "New version available: 1.20.0. Open release notes.",
    });

    expect(link).toHaveAttribute("href", "https://github.com/Soju06/codex-lb/releases/latest");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("does not show an update link when the runtime version check fails", async () => {
    server.use(
      http.get("/api/runtime/version", () =>
        HttpResponse.json({ error: "upstream unavailable" }, { status: 503 }),
      ),
    );

    renderStatusBar();

    expect(await screen.findByText("Version:")).toBeInTheDocument();
    expect(
      screen.queryByRole("link", {
        name: /New version available/,
      }),
    ).not.toBeInTheDocument();
  });
});
