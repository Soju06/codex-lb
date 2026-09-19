import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ProviderFilter } from "@/components/provider-filter";
import { parseProviderFilterValue } from "@/components/provider-filter-options";

describe("parseProviderFilterValue", () => {
  it("parses known providers", () => {
    expect(parseProviderFilterValue("openai")).toBe("openai");
    expect(parseProviderFilterValue("anthropic")).toBe("anthropic");
  });

  it("falls back to all for null or unknown values", () => {
    expect(parseProviderFilterValue(null)).toBe("all");
    expect(parseProviderFilterValue("")).toBe("all");
    expect(parseProviderFilterValue("google")).toBe("all");
  });
});

describe("ProviderFilter", () => {
  it("renders All / Codex / Claude segments with counts", () => {
    render(
      <ProviderFilter
        value="all"
        counts={{ all: 8, openai: 5, anthropic: 3, glm: 0, kimi: 0, openrouter: 0 }}
        onChange={vi.fn()}
      />,
    );

    const buttons = screen.getAllByRole("button");
    expect(buttons.map((button) => button.textContent)).toEqual([
      "All8",
      "Codex5",
      "Claude3",
    ]);
  });

  it("marks only the active segment as pressed", () => {
    render(
      <ProviderFilter
        value="anthropic"
        counts={{ all: 8, openai: 5, anthropic: 3, glm: 0, kimi: 0, openrouter: 0 }}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /Claude/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /^All/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByRole("button", { name: /Codex/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("invokes onChange with the segment value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ProviderFilter
        value="all"
        counts={{ all: 8, openai: 5, anthropic: 3, glm: 0, kimi: 0, openrouter: 0 }}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Codex/ }));
    expect(onChange).toHaveBeenCalledWith("openai");

    await user.click(screen.getByRole("button", { name: /Claude/ }));
    expect(onChange).toHaveBeenCalledWith("anthropic");
  });

  it("keeps a zero-count segment clickable", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ProviderFilter
        value="all"
        counts={{ all: 5, openai: 5, anthropic: 0, glm: 0, kimi: 0, openrouter: 0 }}
        onChange={onChange}
      />,
    );

    const claude = screen.getByRole("button", { name: /Claude/ });
    expect(claude).toBeEnabled();
    expect(claude.textContent).toBe("Claude0");

    await user.click(claude);
    expect(onChange).toHaveBeenCalledWith("anthropic");
  });

  it("renders segments without counts when counts are omitted", () => {
    render(<ProviderFilter value="all" onChange={vi.fn()} />);

    expect(
      screen.getAllByRole("button").map((button) => button.textContent),
    ).toEqual(["All", "Codex", "Claude"]);
  });
});
