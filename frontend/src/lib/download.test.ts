import { describe, expect, it, vi } from "vitest";

import { downloadBlob } from "@/lib/download";

describe("downloadBlob", () => {
  it("creates an object URL, clicks a temporary link, and revokes the URL", () => {
    const blob = new Blob(["zip"]);
    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:test");
    const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    vi.useFakeTimers();
    downloadBlob(blob, "auth-export.zip");
    vi.runAllTimers();

    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:test");

    vi.useRealTimers();
  });
});
