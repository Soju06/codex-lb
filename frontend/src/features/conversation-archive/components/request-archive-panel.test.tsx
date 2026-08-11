import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RequestArchivePanel } from "@/features/conversation-archive/components/request-archive-panel";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";

const hookMocks = vi.hoisted(() => ({
  useConversationArchiveRecords: vi.fn(),
}));

vi.mock("@/features/conversation-archive/hooks/use-conversation-archive", () => hookMocks);

describe("RequestArchivePanel", () => {
  beforeEach(() => {
    useDateDisplayFormatStore.setState({ dateDisplayFormat: "default" });
    hookMocks.useConversationArchiveRecords.mockReturnValue({
      data: {
        records: [
          {
            fileName: null,
            timestamp: "2026-08-09T14:30:45",
            requestId: "request-1",
            direction: "request",
            kind: "body",
            transport: "responses",
            accountId: null,
            method: null,
            url: null,
            statusCode: null,
            headers: null,
            payload: {},
            extra: null,
          },
        ],
        total: 1,
        hasMore: false,
      },
      error: null,
      isError: false,
      isPending: false,
    });
  });

  it("updates a filename-missing record timestamp when ISO 8601 is selected", () => {
    render(<RequestArchivePanel requestId="request-1" />);

    expect(screen.queryByText("2026-08-09 14:30:45")).not.toBeInTheDocument();

    act(() => {
      useDateDisplayFormatStore.setState({ dateDisplayFormat: "iso8601" });
    });

    expect(screen.getByText("2026-08-09 14:30:45")).toBeInTheDocument();
  });
});
