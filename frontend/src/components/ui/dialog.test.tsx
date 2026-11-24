import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

function renderOpenDialog(onOpenChange: (open: boolean) => void) {
  render(
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Confirm</DialogTitle>
        <DialogDescription>Enter your code.</DialogDescription>
        <input aria-label="Code" autoComplete="one-time-code" />
      </DialogContent>
    </Dialog>,
  );
}

// Radix attaches its outside-press listeners on the next tick after mount, and
// settles a primary-button press outside on the click that follows it.
const nextTick = () => new Promise((resolve) => setTimeout(resolve, 0));

async function press(target: Element) {
  fireEvent.pointerDown(target, { button: 0 });
  fireEvent.mouseDown(target, { button: 0 });
  fireEvent.pointerUp(target, { button: 0 });
  fireEvent.mouseUp(target, { button: 0 });
  fireEvent.click(target, { button: 0 });
  await nextTick();
}

describe("Dialog", () => {
  afterEach(() => {
    document.querySelectorAll("[data-third-party]").forEach((node) => node.remove());
  });

  it("stays open when a surface drawn over it by a third party is pressed", async () => {
    const onOpenChange = vi.fn();
    renderOpenDialog(onOpenChange);
    await nextTick();

    // Stand-in for a password manager's suggestion list: injected into <body>,
    // outside both the dialog content and its overlay.
    const suggestion = document.createElement("div");
    suggestion.dataset.thirdParty = "";
    document.body.append(suggestion);
    await press(suggestion);

    expect(onOpenChange).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Confirm" })).toBeInTheDocument();
  });

  it("still closes when the overlay is pressed", async () => {
    const onOpenChange = vi.fn();
    renderOpenDialog(onOpenChange);
    await nextTick();

    const overlay = document.querySelector("[data-slot='dialog-overlay']");
    expect(overlay).not.toBeNull();
    await press(overlay!);

    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
