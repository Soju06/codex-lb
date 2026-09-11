import { describe, expect, it } from "vitest";

import {
  REQUEST_LOG_SOURCE_FILTER_VALUES,
  REQUEST_LOG_SOURCE_OVERFLOW,
  REQUEST_LOG_SOURCE_OVERFLOW_PINNED,
  requestLogSourceKind,
} from "@/features/dashboard/request-log-source";

describe("requestLogSourceKind", () => {
  it.each([
    [REQUEST_LOG_SOURCE_OVERFLOW, "overflow"],
    [REQUEST_LOG_SOURCE_OVERFLOW_PINNED, "overflowPinned"],
  ])("maps %s to %s", (source, expected) => {
    expect(requestLogSourceKind(source)).toBe(expected);
  });

  it.each([
    ["a null source", null],
    ["an undefined source", undefined],
    ["the limit warm-up source", "limit_warmup"],
    ["the warm-up probe source", "warmup_probe"],
    ["an unknown future source", "some_future_source"],
    ["an empty string", ""],
    // Guards against a prefix match sneaking in: only exact values attribute.
    ["a prefixed lookalike", "subscription_overflow_v2"],
  ])("returns null for %s", (_label, source) => {
    expect(requestLogSourceKind(source)).toBeNull();
  });
});

describe("REQUEST_LOG_SOURCE_FILTER_VALUES", () => {
  it("is the closed pair the filter offers, in menu order", () => {
    expect(REQUEST_LOG_SOURCE_FILTER_VALUES).toEqual([
      REQUEST_LOG_SOURCE_OVERFLOW,
      REQUEST_LOG_SOURCE_OVERFLOW_PINNED,
    ]);
  });

  it("only contains values the chip can attribute", () => {
    for (const value of REQUEST_LOG_SOURCE_FILTER_VALUES) {
      expect(requestLogSourceKind(value)).not.toBeNull();
    }
  });
});
