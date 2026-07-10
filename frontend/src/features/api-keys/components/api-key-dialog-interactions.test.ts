import { describe, expect, it, vi } from "vitest";

import { preventApiKeyDialogDropdownDismiss } from "./api-key-dialog-interactions";

describe("preventApiKeyDialogDropdownDismiss", () => {
  it("prevents dismissal for portalled dropdown content", () => {
    const content = document.createElement("div");
    content.dataset.slot = "dropdown-menu-content";
    const item = document.createElement("div");
    content.append(item);
    const preventDefault = vi.fn();

    preventApiKeyDialogDropdownDismiss({ target: item, preventDefault });

    expect(preventDefault).toHaveBeenCalledOnce();
  });

  it("allows ordinary outside interactions", () => {
    const preventDefault = vi.fn();

    preventApiKeyDialogDropdownDismiss({
      target: document.createElement("div"),
      preventDefault,
    });

    expect(preventDefault).not.toHaveBeenCalled();
  });
});
