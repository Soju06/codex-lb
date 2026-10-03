import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiList } from "@/features/apis/components/api-list";
import { createApiKey } from "@/test/mocks/factories";

afterEach(() => vi.restoreAllMocks());

describe("ApiList", () => {
  it.each([
    ["Key", ["Alpha", "Bravo", "Charlie"], ["Charlie", "Bravo", "Alpha"]],
    ["Status", ["Bravo", "Alpha", "Charlie"], ["Charlie", "Alpha", "Bravo"]],
    ["Expires", ["Charlie", "Bravo", "Alpha"], ["Bravo", "Charlie", "Alpha"]],
    ["Last used", ["Bravo", "Charlie", "Alpha"], ["Charlie", "Bravo", "Alpha"]],
  ] as const)("sorts %s through headers and the mobile dropdown", async (column, asc, desc) => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-01T00:00:00Z"));
    const user = userEvent.setup();
    const keys = [
      createApiKey({ id: "a", name: "Alpha", isActive: false, expiresAt: null, lastUsedAt: null }),
      createApiKey({ id: "b", name: "Bravo", isActive: true, expiresAt: "2030-01-01T00:00:00Z", lastUsedAt: "2026-01-01T00:00:00Z" }),
      createApiKey({ id: "c", name: "Charlie", isActive: true, expiresAt: "2020-01-01T00:00:00Z", lastUsedAt: "2026-02-01T00:00:00Z" }),
    ];
    render(<ApiList viewMode="list" apiKeys={keys} selectedKeyId={null} onSelect={() => {}} onOpenCreate={() => {}} />);
    const names = () => screen.getAllByTestId("api-list-overview-row").map((row) => row.getAttribute("aria-label")?.replace("Details for ", ""));
    await user.click(screen.getByRole("button", { name: `${column}: Not sorted` }));
    expect(names()).toEqual(asc);
    await user.click(screen.getByRole("button", { name: `${column}: Ascending` }));
    expect(names()).toEqual(desc);
    await user.click(screen.getByRole("combobox", { name: "Sort API keys" }));
    await user.click(screen.getByRole("option", { name: `${column} (Ascending)` }));
    expect(names()).toEqual(asc);
  });
  it("filters unused keys using timestamps and lifetime requests in both views", async () => {
    const user = userEvent.setup();
    const keys = [
      createApiKey({
        id: "new",
        name: "New key",
        lastUsedAt: null,
        usageSummary: null,
      }),
      createApiKey({
        id: "zero",
        name: "Zero key",
        lastUsedAt: null,
        isActive: false,
        usageSummary: {
          requestCount: 0,
          totalTokens: 0,
          cachedInputTokens: 0,
          totalCostUsd: 0,
        },
      }),
      createApiKey({
        id: "old",
        name: "Old key",
        lastUsedAt: "2026-01-01T00:00:00Z",
        usageSummary: null,
      }),
      createApiKey({
        id: "delayed",
        name: "Delayed key",
        lastUsedAt: null,
        usageSummary: {
          requestCount: 2,
          totalTokens: 0,
          cachedInputTokens: 0,
          totalCostUsd: 0,
        },
      }),
    ];
    const props = {
      apiKeys: keys,
      selectedKeyId: null,
      onSelect: vi.fn(),
      onOpenCreate: vi.fn(),
    };
    const view = render(<ApiList {...props} viewMode="list" />);
    await user.click(
      screen.getByRole("combobox", { name: "Filter keys by usage" }),
    );
    await user.click(screen.getByRole("option", { name: "Key not used" }));
    expect(screen.getAllByTestId("api-list-overview-row")).toHaveLength(2);
    expect(screen.queryByText("Old key")).not.toBeInTheDocument();
    expect(screen.queryByText("Delayed key")).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("combobox", { name: "Filter keys by status" }),
    );
    await user.click(
      screen.getByRole("option", { name: "Disabled" }),
    );
    expect(screen.getAllByTestId("api-list-overview-row")).toHaveLength(1);
    view.rerender(<ApiList {...props} viewMode="detail" />);
    expect(screen.getByText("Zero key")).toBeInTheDocument();
    expect(screen.queryByText("New key")).not.toBeInTheDocument();
    await user.type(
      screen.getByRole("textbox", { name: "Search API keys..." }),
      "absent",
    );
    expect(screen.getByText("No matching API keys")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create API Key" }),
    ).toBeEnabled();
    await user.clear(
      screen.getByRole("textbox", { name: "Search API keys..." }),
    );
    view.rerender(
      <ApiList
        {...props}
        apiKeys={keys.map((key) => ({
          ...key,
          lastUsedAt: "2026-01-01T00:00:00Z",
        }))}
      />,
    );
    expect(screen.getByText("No matching API keys")).toBeInTheDocument();
    await user.click(
      screen.getByRole("combobox", { name: "Filter keys by usage" }),
    );
    await user.click(screen.getByRole("option", { name: "Used" }));
    expect(screen.getByText("Zero key")).toBeInTheDocument();
  });

  it("sorts all matching keys before pagination and retains unknown values last", async () => {
    const user = userEvent.setup();
    const keys = Array.from({ length: 26 }, (_, i) =>
      createApiKey({
        id: `key-${i}`,
        name: `Key ${String(i).padStart(2, "0")}`,
        usageSummary:
          i === 25
            ? null
            : {
                requestCount: i,
                totalTokens: 0,
                cachedInputTokens: 0,
                totalCostUsd: 0,
              },
      }),
    );
    render(
      <ApiList
        apiKeys={keys}
        viewMode="list"
        selectedKeyId={null}
        onSelect={() => {}}
        onOpenCreate={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getAllByTestId("api-list-overview-row")).toHaveLength(2);
    await user.click(
      screen.getByRole("button", { name: "Lifetime requests: Not sorted" }),
    );
    expect(screen.getAllByTestId("api-list-overview-row")[0]).toHaveTextContent(
      "Key 00",
    );
    const header = screen.getByRole("button", {
      name: "Lifetime requests: Ascending",
    });
    header.focus();
    await user.keyboard("{Enter}");
    expect(screen.getAllByTestId("api-list-overview-row")[0]).toHaveTextContent(
      "Key 24",
    );
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(
      screen.getAllByTestId("api-list-overview-row").at(-1),
    ).toHaveTextContent("Key 25");
    await user.type(
      screen.getByRole("textbox", { name: "Search API keys..." }),
      "Key 24",
    );
    expect(screen.getAllByTestId("api-list-overview-row")).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "Previous" }),
    ).toBeDisabled();
  });
  it("shows first-run empty copy when no API keys exist", () => {
    render(
      <ApiList
        apiKeys={[]}
        selectedKeyId={null}
        onSelect={() => {}}
        onOpenCreate={() => {}}
      />,
    );

    expect(screen.getByText("No API keys yet")).toBeInTheDocument();
    expect(
      screen.getByText("Create an API key to authenticate clients."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Adjust filters")).not.toBeInTheDocument();
  });

  it("shows filter-empty copy when keys exist but none match", async () => {
    const user = userEvent.setup();

    render(
      <ApiList
        apiKeys={[createApiKey({ name: "Fleet key", keyPrefix: "sk-fleet" })]}
        selectedKeyId={null}
        onSelect={() => {}}
        onOpenCreate={() => {}}
      />,
    );

    await user.type(
      screen.getByPlaceholderText("Search API keys..."),
      "not-found",
    );

    expect(screen.getByText("No matching API keys")).toBeInTheDocument();
    expect(screen.getByText("Adjust filters")).toBeInTheDocument();
  });
});
