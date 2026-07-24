import { describe, expect, it, vi } from "vitest";
import { screen, render, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ConversationTimeframeSelect } from "@/features/dashboard/components/filters/conversation-timeframe-select";

describe("ConversationTimeframeSelect", () => {
  it("defaults to 7d and exposes the bounded day options", async () => {
    const user = userEvent.setup();
    render(<ConversationTimeframeSelect value="7d" onChange={vi.fn()} />);

    const trigger = screen.getByRole("combobox", { name: "Conversation timeframe" });
    expect(trigger).toHaveTextContent("7d");

    await user.click(trigger);
    const menu = await screen.findByRole("listbox");
    expect(within(menu).getAllByRole("option")).toHaveLength(3);
    expect(menu).toHaveTextContent("1d");
    expect(menu).toHaveTextContent("7d");
    expect(menu).toHaveTextContent("30d");
  });

  it("reports a selected timeframe", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ConversationTimeframeSelect value="7d" onChange={onChange} />);

    await user.click(screen.getByRole("combobox", { name: "Conversation timeframe" }));
    await user.click(await screen.findByRole("option", { name: "30d" }));

    expect(onChange).toHaveBeenCalledWith("30d");
  });
});
