import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { LimitRuleCreate } from "@/features/api-keys/schemas";
import { renderWithProviders } from "@/test/utils";

import { LimitRuleCard } from "./limit-rule-card";

function tokenRule(overrides: Partial<LimitRuleCreate> = {}): LimitRuleCreate {
  return {
    limitType: "total_tokens",
    limitWindow: "weekly",
    maxValue: 100_000,
    modelFilter: null,
    ...overrides,
  };
}

function costRule(overrides: Partial<LimitRuleCreate> = {}): LimitRuleCreate {
  return {
    limitType: "cost_usd",
    limitWindow: "weekly",
    maxValue: 5_000_000,
    modelFilter: null,
    ...overrides,
  };
}

describe("LimitRuleCard", () => {
  it("renders token-type rule with correct label and value", () => {
    renderWithProviders(
      <LimitRuleCard rule={tokenRule()} onChange={vi.fn()} onRemove={vi.fn()} />,
    );

    expect(screen.getByText("Max value (tokens)")).toBeInTheDocument();
    expect(screen.getByDisplayValue("100000")).toBeInTheDocument();
  });

  it("renders cost-type rule with USD label and converted value", () => {
    renderWithProviders(
      <LimitRuleCard rule={costRule()} onChange={vi.fn()} onRemove={vi.fn()} />,
    );

    expect(screen.getByText("Max value (USD)")).toBeInTheDocument();
    expect(screen.getByDisplayValue("5")).toBeInTheDocument();
  });

  it("displays empty string when maxValue is zero", () => {
    renderWithProviders(
      <LimitRuleCard rule={tokenRule({ maxValue: 0 })} onChange={vi.fn()} onRemove={vi.fn()} />,
    );

    const input = screen.getByRole("spinbutton");
    expect(input).toHaveValue(null);
  });

  it("calls onChange with 0 when input is cleared", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(
      <LimitRuleCard rule={tokenRule()} onChange={onChange} onRemove={vi.fn()} />,
    );

    const input = screen.getByDisplayValue("100000");
    await user.clear(input);

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ maxValue: 0 }),
    );
  });

  it("converts cost input to micro-USD", () => {
    const onChange = vi.fn();
    renderWithProviders(
      <LimitRuleCard rule={costRule({ maxValue: 0 })} onChange={onChange} onRemove={vi.fn()} />,
    );

    const input = screen.getByRole("spinbutton");
    fireEvent.change(input, { target: { value: "2.5" } });

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ maxValue: 2_500_000 }),
    );
  });

  it("parses integer token values", () => {
    const onChange = vi.fn();
    renderWithProviders(
      <LimitRuleCard rule={tokenRule({ maxValue: 0 })} onChange={onChange} onRemove={vi.fn()} />,
    );

    const input = screen.getByRole("spinbutton");
    fireEvent.change(input, { target: { value: "500" } });

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ maxValue: 500 }),
    );
  });

  it("calls onRemove when trash button is clicked", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    renderWithProviders(
      <LimitRuleCard rule={tokenRule()} onChange={vi.fn()} onRemove={onRemove} />,
    );

    // Find the ghost/trash button specifically (data-variant="ghost")
    const buttons = screen.getAllByRole("button");
    const trashButton = buttons.find((b) => b.getAttribute("data-variant") === "ghost");
    expect(trashButton).toBeDefined();
    await user.click(trashButton!);
    expect(onRemove).toHaveBeenCalledOnce();
  });

  it("renders model filter placeholder when null", async () => {
    renderWithProviders(
      <LimitRuleCard
        rule={tokenRule({ modelFilter: null })}
        onChange={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    // ModelMultiSelect uses useModels which fetches from MSW
    await waitFor(() => {
      expect(screen.getByText("All models")).toBeInTheDocument();
    });
  });

  it("renders badge for selected model filter", async () => {
    renderWithProviders(
      <LimitRuleCard
        rule={tokenRule({ modelFilter: "gpt-5.1" })}
        onChange={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("1 model selected")).toBeInTheDocument();
    });
  });
});
