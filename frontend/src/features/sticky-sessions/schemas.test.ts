import { describe, expect, it } from "vitest";

import {
  StickySessionEntrySchema,
  StickySessionIdentifierSchema,
  StickySessionsListParamsSchema,
  StickySessionsListResponseSchema,
  StickySessionsPurgeRequestSchema,
} from "@/features/sticky-sessions/schemas";

describe("StickySessionEntrySchema", () => {
  it("parses sticky session metadata", () => {
    const parsed = StickySessionEntrySchema.parse({
      key: "thread_123",
      displayName: "sticky-a@example.com",
      kind: "prompt_cache",
      providerKind: "chatgpt_web",
      routingSubjectId: "acc_sticky_a",
      affinityScope: "provider_prompt_cache",
      createdAt: "2026-03-10T12:00:00Z",
      updatedAt: "2026-03-10T12:05:00Z",
      expiresAt: "2026-03-10T12:10:00Z",
      isStale: false,
    });

    expect(parsed.kind).toBe("prompt_cache");
    expect(parsed.displayName).toBe("sticky-a@example.com");
    expect(parsed.providerKind).toBe("chatgpt_web");
    expect(parsed.routingSubjectId).toBe("acc_sticky_a");
    expect(parsed.expiresAt).toBe("2026-03-10T12:10:00Z");
  });
});

describe("StickySessionsListResponseSchema", () => {
  it("defaults entries and stalePromptCacheCount", () => {
    const parsed = StickySessionsListResponseSchema.parse({});
    expect(parsed.entries).toEqual([]);
    expect(parsed.stalePromptCacheCount).toBe(0);
    expect(parsed.total).toBe(0);
    expect(parsed.hasMore).toBe(false);
  });
});

describe("StickySessionsListParamsSchema", () => {
  it("defaults pagination parameters", () => {
    const parsed = StickySessionsListParamsSchema.parse({});
    expect(parsed).toEqual({
      staleOnly: false,
      providerKind: null,
      accountQuery: "",
      keyQuery: "",
      sortBy: "updated_at",
      sortDir: "desc",
      offset: 0,
      limit: 10,
    });
  });
});

describe("StickySessionIdentifierSchema", () => {
  it("defaults provider kind for composite sticky-session identities", () => {
    const parsed = StickySessionIdentifierSchema.parse({
      key: "thread_123",
      kind: "prompt_cache",
    });

    expect(parsed).toEqual({
      key: "thread_123",
      kind: "prompt_cache",
      providerKind: "chatgpt_web",
    });
  });
});

describe("StickySessionsPurgeRequestSchema", () => {
  it("defaults staleOnly to true", () => {
    const parsed = StickySessionsPurgeRequestSchema.parse({});
    expect(parsed.staleOnly).toBe(true);
  });
});
