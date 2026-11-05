import { describe, expect, it } from "vitest";

import i18n from "@/i18n";
import type { RequestLogUpstreamOption } from "@/features/dashboard/schemas";
import { buildUpstreamOptions } from "@/features/dashboard/upstream-options";

function source(id: string, displayName: string | null, isDeleted = false): RequestLogUpstreamOption {
  return { id, type: "model_source", displayName, modelSourceKind: "openai_compatible", isDeleted };
}

const POOL: RequestLogUpstreamOption = {
  id: "subscription_pool",
  type: "subscription_pool",
  displayName: null,
  modelSourceKind: null,
  isDeleted: false,
};

describe("buildUpstreamOptions", () => {
  const t = i18n.getFixedT("en");

  it("labels the subscription pool and live sources by stable id", () => {
    expect(buildUpstreamOptions([POOL, source("src_vllm", "Local vLLM")], t)).toEqual([
      { value: "subscription_pool", label: "Subscription pool" },
      { value: "src_vllm", label: "Local vLLM" },
    ]);
  });

  it("uses the localized fallback with the id for a deleted source", () => {
    expect(buildUpstreamOptions([source("src_old", null, true)], t)).toEqual([
      { value: "src_old", label: "Deleted model source · src_old" },
    ]);
  });

  it("adds the source id when two live sources share a name", () => {
    expect(buildUpstreamOptions([source("src_a", "vLLM"), source("src_b", "vLLM"), source("src_c", "Other")], t)).toEqual([
      { value: "src_a", label: "vLLM · src_a" },
      { value: "src_b", label: "vLLM · src_b" },
      { value: "src_c", label: "Other" },
    ]);
  });

  it("returns no options for an older response without upstreams", () => {
    expect(buildUpstreamOptions([], t)).toEqual([]);
  });
});
