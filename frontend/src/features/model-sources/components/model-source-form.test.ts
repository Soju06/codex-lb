import { describe, expect, it } from "vitest";
import { mergeReasoningMetadata, parseReasoningEffortsInput } from "./model-source-form";

describe("model-source-form reasoning effort normalization", () => {
  it("trims whitespace and preserves casing for effort values", () => {
    expect(parseReasoningEffortsInput("  Ultra,   xhigh , low  ")).toEqual([
      "Ultra",
      "xhigh",
      "low",
    ]);
  });

  it("preserves casing for declared default reasoning effort", () => {
    const metadata = mergeReasoningMetadata(
      null,
      true,
      ["Ultra", "provider-specific", "xhigh"],
      "  provider-specific  ",
    );
    const parsed = JSON.parse(metadata ?? "{}");

    expect(parsed.supported_reasoning_levels).toEqual(["Ultra", "provider-specific", "xhigh"]);
    expect(parsed.default_reasoning_level).toBe("provider-specific");
  });
});
