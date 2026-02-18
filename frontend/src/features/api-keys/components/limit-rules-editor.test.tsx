import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { LimitRuleCreate } from "@/features/api-keys/schemas";
import { renderWithProviders } from "@/test/utils";

import { LimitRulesEditor } from "./limit-rules-editor";

describe("LimitRulesEditor", () => {
  it("renders basic mode when rules are empty", () => {
    renderWithProviders(<LimitRulesEditor rules={[]} onChange={vi.fn()} />);

    expect(screen.getByText("Weekly token limit")).toBeInTheDocument();
    expect(screen.getByText("Weekly cost limit ($)")).toBeInTheDocument();
  });

  it("renders basic mode for standard weekly rules", () => {
    const rules: LimitRuleCreate[] = [
      { limitType: "total_tokens", limitWindow: "weekly", maxValue: 500_000, modelFilter: null },
    ];
    renderWithProviders(<LimitRulesEditor rules={rules} onChange={vi.fn()} />);

    expect(screen.getByText("Weekly token limit")).toBeInTheDocument();
    expect(screen.getByDisplayValue("500000")).toBeInTheDocument();
  });

  it("starts in advanced mode when rules have non-standard types", () => {
    const rules: LimitRuleCreate[] = [
      { limitType: "input_tokens", limitWindow: "weekly", maxValue: 100, modelFilter: null },
    ];
    renderWithProviders(<LimitRulesEditor rules={rules} onChange={vi.fn()} />);

    expect(screen.getByText("Add limit rule")).toBeInTheDocument();
  });

  it("starts in advanced mode when rules have non-weekly windows", () => {
    const rules: LimitRuleCreate[] = [
      { limitType: "total_tokens", limitWindow: "daily", maxValue: 100, modelFilter: null },
    ];
    renderWithProviders(<LimitRulesEditor rules={rules} onChange={vi.fn()} />);

    expect(screen.getByText("Add limit rule")).toBeInTheDocument();
  });

  it("starts in advanced mode when rules have model filters", () => {
    const rules: LimitRuleCreate[] = [
      { limitType: "total_tokens", limitWindow: "weekly", maxValue: 100, modelFilter: "gpt-5.1" },
    ];
    renderWithProviders(<LimitRulesEditor rules={rules} onChange={vi.fn()} />);

    expect(screen.getByText("Add limit rule")).toBeInTheDocument();
  });

  it("adds a new rule in advanced mode", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const rules: LimitRuleCreate[] = [
      { limitType: "input_tokens", limitWindow: "daily", maxValue: 100, modelFilter: null },
    ];
    renderWithProviders(<LimitRulesEditor rules={rules} onChange={onChange} />);

    await user.click(screen.getByText("Add limit rule"));

    expect(onChange).toHaveBeenCalledWith([
      rules[0],
      { limitType: "total_tokens", limitWindow: "weekly", maxValue: 0, modelFilter: null },
    ]);
  });

  it("shows multi-rule warning when more than one rule exists", () => {
    const rules: LimitRuleCreate[] = [
      { limitType: "input_tokens", limitWindow: "daily", maxValue: 100, modelFilter: null },
      { limitType: "output_tokens", limitWindow: "daily", maxValue: 200, modelFilter: null },
    ];
    renderWithProviders(<LimitRulesEditor rules={rules} onChange={vi.fn()} />);

    expect(screen.getByText(/All rules are applied together/)).toBeInTheDocument();
  });

  it("does not show multi-rule warning with single rule", () => {
    const rules: LimitRuleCreate[] = [
      { limitType: "input_tokens", limitWindow: "daily", maxValue: 100, modelFilter: null },
    ];
    renderWithProviders(<LimitRulesEditor rules={rules} onChange={vi.fn()} />);

    expect(screen.queryByText(/All rules are applied together/)).not.toBeInTheDocument();
  });

  it("handles basic token input change via fireEvent", () => {
    const onChange = vi.fn();
    renderWithProviders(<LimitRulesEditor rules={[]} onChange={onChange} />);

    const inputs = screen.getAllByRole("spinbutton");
    fireEvent.change(inputs[0], { target: { value: "1000" } });

    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    const tokenRule = lastCall.find(
      (r: LimitRuleCreate) => r.limitType === "total_tokens",
    );
    expect(tokenRule).toBeDefined();
    expect(tokenRule.maxValue).toBe(1000);
  });

  it("handles basic cost input change via fireEvent", () => {
    const onChange = vi.fn();
    renderWithProviders(<LimitRulesEditor rules={[]} onChange={onChange} />);

    const inputs = screen.getAllByRole("spinbutton");
    fireEvent.change(inputs[1], { target: { value: "5" } });

    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    const costRule = lastCall.find(
      (r: LimitRuleCreate) => r.limitType === "cost_usd",
    );
    expect(costRule).toBeDefined();
    expect(costRule.maxValue).toBe(5_000_000);
  });

  it("removes token rule when cleared in basic mode", () => {
    const onChange = vi.fn();
    const rules: LimitRuleCreate[] = [
      { limitType: "total_tokens", limitWindow: "weekly", maxValue: 1000, modelFilter: null },
    ];
    renderWithProviders(<LimitRulesEditor rules={rules} onChange={onChange} />);

    const inputs = screen.getAllByRole("spinbutton");
    fireEvent.change(inputs[0], { target: { value: "" } });

    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("displays cost value converted from micro-USD in basic mode", () => {
    const rules: LimitRuleCreate[] = [
      { limitType: "cost_usd", limitWindow: "weekly", maxValue: 3_500_000, modelFilter: null },
    ];
    renderWithProviders(<LimitRulesEditor rules={rules} onChange={vi.fn()} />);

    expect(screen.getByDisplayValue("3.5")).toBeInTheDocument();
  });

  it("can switch to advanced mode", async () => {
    const user = userEvent.setup();
    renderWithProviders(<LimitRulesEditor rules={[]} onChange={vi.fn()} />);

    expect(screen.getByText("Weekly token limit")).toBeInTheDocument();

    const advancedSwitch = screen.getByRole("switch");
    await user.click(advancedSwitch);

    expect(screen.getByText("Add limit rule")).toBeInTheDocument();
  });
});
