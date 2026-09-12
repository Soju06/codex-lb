import { describe, expect, it } from "vitest";

import * as requestLogSourceModule from "@/features/dashboard/request-log-source";
import {
  REQUEST_LOG_SOURCE_FILTER_VALUES,
  REQUEST_LOG_SOURCE_KINDS,
  REQUEST_LOG_SOURCE_OVERFLOW,
  REQUEST_LOG_SOURCE_OVERFLOW_PINNED,
  requestLogSourceKind,
} from "@/features/dashboard/request-log-source";
import en from "@/i18n/locales/en.json";
import ko from "@/i18n/locales/ko.json";
import zhCN from "@/i18n/locales/zh-CN.json";

/**
 * Every exported `REQUEST_LOG_SOURCE_*` string constant. The backend enum in
 * `app/modules/proxy/overflow.py` is the authority for these values and
 * `tests/unit/test_request_log_source_parity.py` fails when they drift from it;
 * the tests below keep the frontend half internally closed, so a literal or a
 * label removed here fails the frontend suite too rather than only pytest.
 */
const EXPORTED_SOURCE_LITERALS = Object.entries(requestLogSourceModule)
  .filter(([name, value]) => name.startsWith("REQUEST_LOG_SOURCE_") && typeof value === "string")
  .map(([, value]) => value as string);

const LOCALES: ReadonlyArray<[string, Record<string, string>]> = [
  ["en", en],
  ["ko", ko],
  ["zh-CN", zhCN],
];

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

  it("offers every exported source literal", () => {
    expect([...REQUEST_LOG_SOURCE_FILTER_VALUES].sort()).toEqual([...EXPORTED_SOURCE_LITERALS].sort());
  });
});

describe("REQUEST_LOG_SOURCE_KINDS", () => {
  it("is exactly the set of kinds the mapping can return", () => {
    const mapped = EXPORTED_SOURCE_LITERALS.map((value) => requestLogSourceKind(value));

    expect(mapped).not.toContain(null);
    expect([...new Set(mapped)].sort()).toEqual([...REQUEST_LOG_SOURCE_KINDS].sort());
    // One kind per value: a duplicated kind would collapse two chips into one.
    expect(new Set(mapped).size).toBe(EXPORTED_SOURCE_LITERALS.length);
  });

  // The locale resources are flat maps whose keys contain dots, so assert
  // against the key list rather than a `toHaveProperty` dot path.
  it.each(LOCALES)("has a chip label and tooltip for every kind in %s", (_locale, resource) => {
    const keys = Object.keys(resource);

    for (const kind of REQUEST_LOG_SOURCE_KINDS) {
      // The detail dialog reuses the chip label key; the tooltip is chip-only.
      expect(keys).toContain(`dashboard.requests.source.${kind}`);
      expect(keys).toContain(`dashboard.requests.source.${kind}Title`);
    }
  });

  it.each(LOCALES)("has a Source filter option label for every kind in %s", (_locale, resource) => {
    const keys = Object.keys(resource);

    for (const kind of REQUEST_LOG_SOURCE_KINDS) {
      // `dashboard-page.tsx` labels option `n` with the `n`th kind; the Python
      // guard checks the exact key that file passes to `t()`.
      const suffix = `${kind.charAt(0).toUpperCase()}${kind.slice(1)}`;
      expect(keys).toContain(`dashboard.filters.source${suffix}`);
    }
  });
});
