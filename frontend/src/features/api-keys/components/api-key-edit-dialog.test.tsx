import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { LimitRuleCreate } from "@/features/api-keys/schemas";
import { createApiKey } from "@/test/mocks/factories";
import { renderWithProviders } from "@/test/utils";

import { ApiKeyEditDialog } from "./api-key-edit-dialog";
import { hasLimitRuleChanges } from "./limit-rules-utils";

describe("ApiKeyEditDialog", () => {
  it("omits limits from payload when only name changes", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const apiKey = createApiKey();

    renderWithProviders(
      <ApiKeyEditDialog
        open
        busy={false}
        apiKey={apiKey}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    const nameInput = screen.getByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, "Renamed key");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.name).toBe("Renamed key");
    expect("assignedAccountIds" in payload).toBe(false);
    expect("limits" in payload).toBe(false);
  });

  it("omits limits from payload when only isActive changes", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const apiKey = createApiKey();

    renderWithProviders(
      <ApiKeyEditDialog
        open
        busy={false}
        apiKey={apiKey}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    const activeRow = screen.getByText("Active").closest("div");
    if (!activeRow) throw new Error("Active row not found");
    await user.click(within(activeRow).getByRole("switch"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.isActive).toBe(false);
    expect("assignedAccountIds" in payload).toBe(false);
    expect("limits" in payload).toBe(false);
  });

  it("includes limits when actual limit values change", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const apiKey = createApiKey();

    renderWithProviders(
      <ApiKeyEditDialog
        open
        busy={false}
        apiKey={apiKey}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    const tokenLimitInput = screen.getByDisplayValue(
      String(apiKey.limits[0].maxValue),
    );
    await user.clear(tokenLimitInput);
    await user.type(tokenLimitInput, "999999");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const payload = onSubmit.mock.calls[0][0];
    expect("assignedAccountIds" in payload).toBe(false);
    expect(payload.limits).toEqual([
      {
        limitType: "total_tokens",
        limitWindow: "weekly",
        maxValue: 999999,
        modelFilter: null,
      },
    ]);
  });

  it("keeps the dialog open when submit fails", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockRejectedValue(new Error("boom update"));
    const onOpenChange = vi.fn();

    renderWithProviders(
      <ApiKeyEditDialog
        open
        busy={false}
        apiKey={createApiKey()}
        onOpenChange={onOpenChange}
        onSubmit={onSubmit}
      />,
    );

    const nameInput = screen.getByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, "Renamed key");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(
      screen.getByRole("dialog", { name: "Edit API key" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Renamed key");
  });

  it("submits selected assigned accounts", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <ApiKeyEditDialog
        open
        busy={false}
        apiKey={createApiKey()}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await user.click(
      await screen.findByRole("button", { name: "All accounts" }),
    );
    await user.click(
      screen.getByRole("menuitemcheckbox", { name: /primary@example\.com/i }),
    );
    await user.click(
      screen.getByRole("menuitemcheckbox", { name: /secondary@example\.com/i }),
    );
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.assignedAccountIds).toEqual([
      "acc_primary",
      "acc_secondary",
    ]);
  });

  it("submits traffic class", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <ApiKeyEditDialog
        open
        busy={false}
        apiKey={createApiKey({ trafficClass: "opportunistic" })}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    expect(
      screen.getByRole("combobox", { name: "Traffic class" }),
    ).toHaveTextContent("Opportunistic");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.trafficClass).toBe("opportunistic");
  });

  it("omits assigned accounts when editing an unrelated field", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <ApiKeyEditDialog
        open
        busy={false}
        apiKey={createApiKey({ assignedAccountIds: ["acc_primary"] })}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    const nameInput = screen.getByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, "Renamed key");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.name).toBe("Renamed key");
    expect("assignedAccountIds" in payload).toBe(false);
  });

  it("submits an empty assigned account list when clearing scoped accounts", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <ApiKeyEditDialog
        open
        busy={false}
        apiKey={createApiKey({ assignedAccountIds: ["acc_primary"] })}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await user.click(
      await screen.findByRole("button", { name: "1 account selected" }),
    );
    await user.click(
      screen.getByRole("menuitemcheckbox", { name: "All accounts" }),
    );
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.assignedAccountIds).toEqual([]);
  });

  it("submits an empty assigned account list when a scoped key loses its assigned accounts", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <ApiKeyEditDialog
        open
        busy={false}
        apiKey={createApiKey({
          accountAssignmentScopeEnabled: true,
          assignedAccountIds: [],
        })}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    const nameInput = screen.getByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, "Renamed key");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.name).toBe("Renamed key");
    expect(payload.assignedAccountIds).toEqual([]);
  });
});

describe("hasLimitRuleChanges", () => {
  it("treats reordered identical rule sets as unchanged", () => {
    const initial: LimitRuleCreate[] = [
      {
        limitType: "total_tokens",
        limitWindow: "weekly",
        maxValue: 1000,
        modelFilter: null,
      },
      {
        limitType: "cost_usd",
        limitWindow: "monthly",
        maxValue: 1_500_000,
        modelFilter: "gpt-5.1",
      },
    ];
    const reordered: LimitRuleCreate[] = [initial[1], initial[0]];

    expect(hasLimitRuleChanges(initial, reordered)).toBe(false);
  });
});
