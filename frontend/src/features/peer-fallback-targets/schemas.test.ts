import { describe, expect, it } from "vitest";

import {
  PeerFallbackTargetCreateRequestSchema,
  PeerFallbackTargetSchema,
  PeerFallbackTargetUpdateRequestSchema,
  PeerFallbackTargetsResponseSchema,
} from "@/features/peer-fallback-targets/schemas";

describe("PeerFallbackTargetSchema", () => {
  it("parses a peer fallback target", () => {
    const parsed = PeerFallbackTargetSchema.parse({
      id: "peer_1",
      baseUrl: "https://peer.example.com",
      enabled: true,
      createdAt: "2026-04-28T00:00:00Z",
      updatedAt: "2026-04-28T00:00:00Z",
    });

    expect(parsed.baseUrl).toBe("https://peer.example.com");
    expect(parsed.enabled).toBe(true);
  });

  it("defaults missing response targets to an empty list", () => {
    const parsed = PeerFallbackTargetsResponseSchema.parse({});

    expect(parsed.targets).toEqual([]);
  });
});

describe("PeerFallbackTarget request schemas", () => {
  it("accepts create and update payloads", () => {
    const createPayload = PeerFallbackTargetCreateRequestSchema.parse({
      baseUrl: "http://127.0.0.1:2456",
    });
    const updatePayload = PeerFallbackTargetUpdateRequestSchema.parse({
      enabled: false,
    });

    expect(createPayload.baseUrl).toBe("http://127.0.0.1:2456");
    expect(updatePayload.enabled).toBe(false);
  });
});
