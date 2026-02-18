import { describe, expect, it } from "vitest";

import { getErrorMessage, getErrorMessageOrNull } from "@/utils/errors";

describe("errors utils", () => {
  it("returns message from Error", () => {
    expect(getErrorMessage(new Error("boom"))).toBe("boom");
  });

  it("returns fallback for non-Error values", () => {
    expect(getErrorMessage("bad", "Fallback")).toBe("Fallback");
  });

  it("returns null for empty input and fallback for unknown error", () => {
    expect(getErrorMessageOrNull(null)).toBeNull();
    expect(getErrorMessageOrNull(undefined)).toBeNull();
    expect(getErrorMessageOrNull("x", "Fallback")).toBe("Fallback");
  });
});
