import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StickySessionsSection } from "@/features/sticky-sessions/components/sticky-sessions-section";
import { useStickySessions } from "@/features/sticky-sessions/hooks/use-sticky-sessions";

vi.mock("@/features/sticky-sessions/hooks/use-sticky-sessions", () => ({
  useStickySessions: vi.fn(),
}));

const useStickySessionsMock = vi.mocked(useStickySessions);

describe("StickySessionsSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders rows and supports purge and remove actions", async () => {
    const user = userEvent.setup();
    const deleteMutation = {
      mutateAsync: vi.fn().mockResolvedValue(undefined),
      isPending: false,
      error: null,
    };
    const purgeMutation = {
      mutateAsync: vi.fn().mockResolvedValue(undefined),
      isPending: false,
      error: null,
    };

    useStickySessionsMock.mockReturnValue({
      params: {
        staleOnly: false,
        offset: 0,
        limit: 10,
      },
      setOffset: vi.fn(),
      setLimit: vi.fn(),
      stickySessionsQuery: {
        data: {
          entries: [
            {
              key: "session-1",
              displayName: "sticky-a@example.com",
              kind: "prompt_cache",
              createdAt: "2026-03-10T12:00:00Z",
              updatedAt: "2026-03-10T12:05:00Z",
              expiresAt: "2026-03-10T12:10:00Z",
              isStale: true,
            },
            {
              key: "session-2",
              displayName: "sticky-b@example.com",
              kind: "codex_session",
              createdAt: "2026-03-10T12:00:00Z",
              updatedAt: "2026-03-10T12:05:00Z",
              expiresAt: null,
              isStale: false,
            },
          ],
          stalePromptCacheCount: 1,
          total: 2,
          hasMore: false,
        },
        isLoading: false,
        error: null,
      },
      deleteMutation,
      purgeMutation,
    } as never);

    render(<StickySessionsSection />);

    expect(screen.getByText("Prompt cache")).toBeInTheDocument();
    expect(screen.getByText("Codex session")).toBeInTheDocument();
    expect(screen.getByText("sticky-a@example.com")).toBeInTheDocument();
    expect(screen.getByText("sticky-b@example.com")).toBeInTheDocument();
    expect(screen.getByText("Stale")).toBeInTheDocument();
    expect(screen.getByText("Durable")).toBeInTheDocument();
    expect(screen.getByText("Visible rows")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("1–2 of 2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Purge stale" }));
    await user.click(screen.getByRole("button", { name: "Purge" }));

    await waitFor(() => {
      expect(purgeMutation.mutateAsync).toHaveBeenCalledWith(true);
    });

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]!);
    await user.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => {
      expect(deleteMutation.mutateAsync).toHaveBeenCalledWith({
        key: "session-1",
        kind: "prompt_cache",
      });
    });
  });

  it("keeps stale purge enabled when hidden rows are stale", () => {
    const setOffset = vi.fn();
    const setLimit = vi.fn();
    useStickySessionsMock.mockReturnValue({
      params: {
        staleOnly: false,
        offset: 0,
        limit: 10,
      },
      setOffset,
      setLimit,
      stickySessionsQuery: {
        data: {
          entries: [
            {
              key: "session-2",
              displayName: "sticky-b@example.com",
              kind: "codex_session",
              createdAt: "2026-03-10T12:00:00Z",
              updatedAt: "2026-03-10T12:05:00Z",
              expiresAt: null,
              isStale: false,
            },
          ],
          stalePromptCacheCount: 3,
          total: 11,
          hasMore: true,
        },
        isLoading: false,
        error: null,
      },
      deleteMutation: {
        mutateAsync: vi.fn(),
        isPending: false,
        error: null,
      },
      purgeMutation: {
        mutateAsync: vi.fn(),
        isPending: false,
        error: null,
      },
    } as never);

    render(<StickySessionsSection />);

    expect(screen.getByText("11")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Purge stale" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Next page" })).toBeEnabled();
  });

  it("shows pagination controls and advances pagination", async () => {
    const user = userEvent.setup();
    const setOffset = vi.fn();

    useStickySessionsMock.mockReturnValue({
      params: {
        staleOnly: false,
        offset: 0,
        limit: 10,
      },
      setOffset,
      setLimit: vi.fn(),
      stickySessionsQuery: {
        data: {
          entries: [
            {
              key: "session-2",
              displayName: "sticky-b@example.com",
              kind: "codex_session",
              createdAt: "2026-03-10T12:00:00Z",
              updatedAt: "2026-03-10T12:05:00Z",
              expiresAt: null,
              isStale: false,
            },
          ],
          stalePromptCacheCount: 0,
          total: 20,
          hasMore: true,
        },
        isLoading: false,
        error: null,
      },
      deleteMutation: {
        mutateAsync: vi.fn(),
        isPending: false,
        error: null,
      },
      purgeMutation: {
        mutateAsync: vi.fn(),
        isPending: false,
        error: null,
      },
    } as never);

    render(<StickySessionsSection />);

    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(setOffset).toHaveBeenCalledWith(10);

    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.getByText("1–10 of 20")).toBeInTheDocument();
  });
});
