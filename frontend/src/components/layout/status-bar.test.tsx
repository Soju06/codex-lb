import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { StatusBar } from "@/components/layout/status-bar";
import i18n from "@/i18n";
import { createDashboardSettings } from "@/test/mocks/factories";
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

function mockSettings(
  overrides: Parameters<typeof createDashboardSettings>[0] = {},
) {
  server.use(
    http.get("/api/settings", () => HttpResponse.json(createDashboardSettings(overrides))),
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

  it("localizes combined routing labels in zh-CN", async () => {
    await i18n.changeLanguage("zh-CN");
    try {
      mockSettings({
        routingStrategy: "capacity_weighted",
        stickyThreadsEnabled: true,
        preferEarlierResetAccounts: true,
        preferEarlierResetWindow: "secondary",
      });

      renderStatusBar();

      expect(await screen.findByText(/按容量加权/)).toBeInTheDocument();
      expect(screen.getByText(/粘性/)).toBeInTheDocument();
      expect(screen.getByText(/较早周重置/)).toBeInTheDocument();
      expect(screen.queryByText(/Capacity weighted/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Sticky threads/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Early weekly reset/)).not.toBeInTheDocument();
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("does not show early reset for strategies that do not honor it", async () => {
    mockSettings({
      routingStrategy: "round_robin",
      stickyThreadsEnabled: false,
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "secondary",
    });

    renderStatusBar();

    expect(await screen.findByText("Round robin")).toBeInTheDocument();
    expect(screen.queryByText("Early weekly reset")).not.toBeInTheDocument();
    expect(screen.queryByText("Round robin + Early weekly reset")).not.toBeInTheDocument();
  });

  it("keeps single-account routing label unadorned", async () => {
    mockSettings({
      routingStrategy: "single_account",
      stickyThreadsEnabled: true,
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "secondary",
    });

    renderStatusBar();

    expect(await screen.findByText("Single account")).toBeInTheDocument();
    expect(screen.queryByText("Single account + Sticky threads")).not.toBeInTheDocument();
    expect(screen.queryByText("Single account + Sticky + Early weekly reset")).not.toBeInTheDocument();
  });

  it("still shows early reset for supported strategies", async () => {
    mockSettings({
      routingStrategy: "fill_first",
      stickyThreadsEnabled: false,
      preferEarlierResetAccounts: true,
      preferEarlierResetWindow: "secondary",
    });

    renderStatusBar();

    expect(await screen.findByText("Fill first + Early weekly reset")).toBeInTheDocument();
  });
});
