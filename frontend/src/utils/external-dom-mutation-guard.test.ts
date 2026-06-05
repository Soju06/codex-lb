import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installExternalDomMutationGuard } from "@/utils/external-dom-mutation-guard";

describe("installExternalDomMutationGuard", () => {
  let originalRemoveChild: typeof Node.prototype.removeChild;
  let originalInsertBefore: typeof Node.prototype.insertBefore;

  beforeEach(() => {
    vi.resetModules();
    originalRemoveChild = Node.prototype.removeChild;
    originalInsertBefore = Node.prototype.insertBefore;
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  afterEach(() => {
    Node.prototype.removeChild = originalRemoveChild;
    Node.prototype.insertBefore = originalInsertBefore;
    vi.restoreAllMocks();
  });

  it("returns externally moved children instead of throwing on removeChild", async () => {
    const { installExternalDomMutationGuard } = await import("@/utils/external-dom-mutation-guard");
    const parent = document.createElement("div");
    const otherParent = document.createElement("div");
    const child = document.createElement("span");
    otherParent.appendChild(child);

    installExternalDomMutationGuard();

    expect(parent.removeChild(child)).toBe(child);
    expect(console.error).toHaveBeenCalledWith(
      "Ignoring external DOM mutation before removeChild",
      child,
      parent,
    );
  });

  it("returns new nodes instead of throwing when insertBefore reference moved", async () => {
    const { installExternalDomMutationGuard } = await import("@/utils/external-dom-mutation-guard");
    const parent = document.createElement("div");
    const otherParent = document.createElement("div");
    const reference = document.createElement("span");
    const newNode = document.createElement("strong");
    otherParent.appendChild(reference);

    installExternalDomMutationGuard();

    expect(parent.insertBefore(newNode, reference)).toBe(newNode);
    expect(parent.contains(newNode)).toBe(false);
    expect(console.error).toHaveBeenCalledWith(
      "Ignoring external DOM mutation before insertBefore",
      reference,
      parent,
    );
  });

  it("leaves valid DOM operations intact", async () => {
    const { installExternalDomMutationGuard } = await import("@/utils/external-dom-mutation-guard");
    const parent = document.createElement("div");
    const child = document.createElement("span");
    const newNode = document.createElement("strong");
    parent.appendChild(child);

    installExternalDomMutationGuard();

    expect(parent.insertBefore(newNode, child)).toBe(newNode);
    expect(parent.firstChild).toBe(newNode);
    expect(parent.removeChild(child)).toBe(child);
    expect(console.error).not.toHaveBeenCalled();
  });

  it("installs only once", () => {
    installExternalDomMutationGuard();
    const removeChild = Node.prototype.removeChild;
    const insertBefore = Node.prototype.insertBefore;

    installExternalDomMutationGuard();

    expect(Node.prototype.removeChild).toBe(removeChild);
    expect(Node.prototype.insertBefore).toBe(insertBefore);
  });
});
