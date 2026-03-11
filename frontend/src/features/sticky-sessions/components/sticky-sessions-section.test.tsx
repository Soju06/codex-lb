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
      stickySessionsQuery: {
        data: {
          entries: [
            {
              key: "session-1",
              accountId: "acc_1",
              kind: "prompt_cache",
              createdAt: "2026-03-10T12:00:00Z",
              updatedAt: "2026-03-10T12:05:00Z",
              expiresAt: "2026-03-10T12:10:00Z",
              isStale: true,
            },
            {
              key: "session-2",
              accountId: "acc_2",
              kind: "codex_session",
              createdAt: "2026-03-10T12:00:00Z",
              updatedAt: "2026-03-10T12:05:00Z",
              expiresAt: null,
              isStale: false,
            },
          ],
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
    expect(screen.getByText("Stale")).toBeInTheDocument();
    expect(screen.getByText("Durable")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Purge stale" }));
    await user.click(screen.getByRole("button", { name: "Purge" }));

    await waitFor(() => {
      expect(purgeMutation.mutateAsync).toHaveBeenCalledWith(true);
    });

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]!);
    await user.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => {
      expect(deleteMutation.mutateAsync).toHaveBeenCalledWith("session-1");
    });
  });

  it("renders the empty state when there are no sticky sessions", () => {
    useStickySessionsMock.mockReturnValue({
      stickySessionsQuery: {
        data: { entries: [] },
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

    expect(screen.getByText("No sticky sessions")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Purge stale" })).toBeDisabled();
  });
});
