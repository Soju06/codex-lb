import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiKeyTable } from "@/features/api-keys/components/api-key-table";
import { createApiKey, createDefaultApiKeys } from "@/test/mocks/factories";
import { renderWithProviders } from "@/test/utils";

describe("ApiKeyTable", () => {
  it("renders the empty state when no keys are provided", () => {
    renderWithProviders(
      <ApiKeyTable
        keys={[]}
        busy={false}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRegenerate={vi.fn()}
      />,
    );

    expect(screen.getByText("No API keys created yet")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("renders one row per key with prefix, status, and limit/usage summaries", () => {
    const keys = createDefaultApiKeys();
    renderWithProviders(
      <ApiKeyTable
        keys={keys}
        busy={false}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRegenerate={vi.fn()}
      />,
    );

    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(keys.length + 1);

    const firstRow = rows[1];
    expect(within(firstRow).getByText(keys[0].name)).toBeInTheDocument();
    expect(within(firstRow).getByText(keys[0].keyPrefix)).toBeInTheDocument();
    expect(within(firstRow).getByText("Active")).toBeInTheDocument();

    const secondRow = rows[2];
    expect(within(secondRow).getByText("Disabled")).toBeInTheDocument();
    expect(within(secondRow).getByText("No Limit")).toBeInTheDocument();
  });

  it("renders traffic class labels in the traffic column", () => {
    const keys = [
      createApiKey({ id: "foreground-key", trafficClass: "foreground" }),
      createApiKey({
        id: "opportunistic-key",
        name: "Opportunistic key",
        trafficClass: "opportunistic",
      }),
    ];
    renderWithProviders(
      <ApiKeyTable
        keys={keys}
        busy={false}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRegenerate={vi.fn()}
      />,
    );

    expect(screen.getByText("Foreground")).toBeInTheDocument();
    expect(screen.getByText("Opportunistic")).toBeInTheDocument();
  });

  it("renders 'All' when allowedModels is missing and the configured list otherwise", () => {
    const restricted = createApiKey({
      id: "key_restricted",
      allowedModels: ["gpt-5.1", "gpt-4o-mini"],
    });
    const unrestricted = createApiKey({
      id: "key_unrestricted",
      name: "Unrestricted key",
      keyPrefix: "sk-open",
      allowedModels: null,
    });

    renderWithProviders(
      <ApiKeyTable
        keys={[restricted, unrestricted]}
        busy={false}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRegenerate={vi.fn()}
      />,
    );

    expect(screen.getByText("gpt-5.1, gpt-4o-mini")).toBeInTheDocument();
    expect(screen.getByText("All")).toBeInTheDocument();
  });

  it("invokes onEdit / onRegenerate / onDelete with the selected key from the row menu", async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    const onRegenerate = vi.fn();
    const onDelete = vi.fn();
    const target = createApiKey({ id: "key_target", name: "Target key" });

    renderWithProviders(
      <ApiKeyTable
        keys={[target]}
        busy={false}
        onEdit={onEdit}
        onDelete={onDelete}
        onRegenerate={onRegenerate}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(screen.getByRole("menuitem", { name: /edit/i }));
    expect(onEdit).toHaveBeenCalledWith(target);

    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(screen.getByRole("menuitem", { name: /regenerate/i }));
    expect(onRegenerate).toHaveBeenCalledWith(target);

    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(screen.getByRole("menuitem", { name: /delete/i }));
    expect(onDelete).toHaveBeenCalledWith(target);
  });

  it("disables the row action trigger when busy is true", () => {
    renderWithProviders(
      <ApiKeyTable
        keys={[createApiKey()]}
        busy={true}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRegenerate={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Actions" })).toBeDisabled();
  });
});
