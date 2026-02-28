import { describe, expect, it } from "vitest";

import { FirewallIpCreateRequestSchema, FirewallIpsResponseSchema } from "@/features/firewall/schemas";

const ISO = "2026-02-18T12:00:00Z";

describe("firewall schemas", () => {
  it("parses firewall list payload", () => {
    const parsed = FirewallIpsResponseSchema.parse({
      mode: "allowlist_active",
      entries: [{ ipAddress: "127.0.0.1", createdAt: ISO }],
    });
    expect(parsed.mode).toBe("allowlist_active");
    expect(parsed.entries).toHaveLength(1);
  });

  it("rejects invalid mode", () => {
    const result = FirewallIpsResponseSchema.safeParse({
      mode: "invalid",
      entries: [],
    });
    expect(result.success).toBe(false);
  });

  it("validates create payload", () => {
    expect(FirewallIpCreateRequestSchema.parse({ ipAddress: "127.0.0.1" }).ipAddress).toBe("127.0.0.1");
  });
});
