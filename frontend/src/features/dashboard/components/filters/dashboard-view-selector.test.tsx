import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DashboardViewSelector } from "@/features/dashboard/components/filters/dashboard-view-selector";

describe("DashboardViewSelector", () => {
  it("renders the active Request Logs label by default", () => {
    render(<DashboardViewSelector value="request-logs" onChange={vi.fn()} />);

    const trigger = screen.getByRole("button", { name: /request logs/i });
    expect(trigger).toBeInTheDocument();
    expect(trigger.querySelector("svg")).toHaveClass("motion-reduce:transition-none", "motion-reduce:transform-none");
  });

  it("renders the active Conversations label when selected", () => {
    render(<DashboardViewSelector value="conversations" onChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: /conversations/i })).toBeInTheDocument();
  });

  it("opens the menu and exposes both options", async () => {
    const user = userEvent.setup();
    render(<DashboardViewSelector value="request-logs" onChange={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /request logs/i }));

    const menu = await screen.findByRole("menu");
    const items = withinMenu(menu).getAllByRole("menuitemradio");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent(/Request Logs/);
    expect(items[1]).toHaveTextContent(/Conversations/);
  });

  it("marks the active option as checked", async () => {
    const user = userEvent.setup();
    render(<DashboardViewSelector value="conversations" onChange={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /conversations/i }));

    const items = await screen.findAllByRole("menuitemradio");
    expect(items[1]).toHaveAttribute("aria-checked", "true");
    expect(items[0]).toHaveAttribute("aria-checked", "false");
  });

  it("fires onChange with conversations and closes the menu on select", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<DashboardViewSelector value="request-logs" onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: /request logs/i }));
    const items = await screen.findAllByRole("menuitemradio");
    await user.click(items[1]);

    expect(onChange).toHaveBeenCalledWith("conversations");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("supports keyboard activation via the trigger", async () => {
    const user = userEvent.setup();
    render(<DashboardViewSelector value="request-logs" onChange={vi.fn()} />);

    const trigger = screen.getByRole("button", { name: /request logs/i });
    trigger.focus();
    await user.keyboard("{Enter}");

    expect(await screen.findByRole("menu")).toBeInTheDocument();
  });
});

function withinMenu(menu: HTMLElement) {
  return {
    getAllByRole: (role: string) =>
      menu.querySelectorAll(`[role="${role}"]`) as NodeListOf<HTMLElement>,
  };
}
