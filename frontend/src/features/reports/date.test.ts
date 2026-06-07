import { describe, expect, it } from "vitest";

import { formatReportBucketDate, localDateISO } from "./date";

describe("reports date helpers", () => {
  it("formats local calendar dates without UTC day shifts", () => {
    const eveningBehindUtc = new Date(2026, 5, 1, 20, 30, 0);

    expect(localDateISO(eveningBehindUtc)).toBe("2026-06-01");
  });

  it("formats report bucket strings without parsing them as UTC instants", () => {
    expect(formatReportBucketDate("2026-06-01")).toBe("01/06");
  });
});
