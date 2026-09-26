import { describe, expect, it } from "vitest";
import { initialModelSourceDraft, modelInputsFromForm, modelSourceFormSchema, mergeReasoningMetadata, parseReasoningEffortsInput } from "./model-source-form";

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

describe("model-source aliases", () => {
  const values = { name: "source", baseUrl: "https://example.test/v1", apiKey: "" };

  it("creates mixed aliases and identity models with reasoning capabilities", () => {
    const parsed = modelSourceFormSchema.parse({ ...values, models: "cd/gpt-6-astra = cd/linxaq, plain" });
    const models = modelInputsFromForm(parsed, {
      ...initialModelSourceDraft, supportsReasoning: true, reasoningEfforts: ["high"], defaultReasoningEffort: "high",
    });
    expect(models.map((model) => model.model)).toEqual(["cd/gpt-6-astra", "plain"]);
    expect(JSON.parse(models[0].rawMetadataJson!)).toMatchObject({ upstream_model: "cd/linxaq", supports_reasoning: true });
    expect(JSON.parse(models[1].rawMetadataJson!)).not.toHaveProperty("upstream_model");
  });

  it.each(["=target", "alias=", "alias=a=b", "alias=a, alias=b", " , ", `alias=${"x".repeat(256)}`])(
    "rejects malformed or duplicate aliases: %s", (models) => {
      expect(modelSourceFormSchema.safeParse({ ...values, models }).success).toBe(false);
    },
  );
});
