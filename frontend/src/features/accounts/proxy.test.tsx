import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api-client";
import {
  AccountProxyInputSchema,
  AccountProxySummarySchema,
} from "@/features/accounts/schemas";
import { formatProbeError, probeReasonFromError } from "@/features/accounts/proxy-errors";

const proxyUserFixture = "proxy-user-fixture";

describe("AccountProxyInputSchema", () => {
  it("parses a minimal payload with defaults", () => {
    const parsed = AccountProxyInputSchema.parse({
      host: "proxy.example.com",
      port: 1080,
    });
    expect(parsed.host).toBe("proxy.example.com");
    expect(parsed.port).toBe(1080);
    expect(parsed.remoteDns).toBe(true);
    expect(parsed.username).toBeUndefined();
    expect(parsed.password).toBeUndefined();
    expect(parsed.clearPassword).toBe(false);
    expect(parsed.label).toBeUndefined();
  });

  it("trims whitespace from host", () => {
    const parsed = AccountProxyInputSchema.parse({
      host: "  proxy.example.com ",
      port: 1080,
    });
    expect(parsed.host).toBe("proxy.example.com");
  });

  it("rejects an empty host", () => {
    const result = AccountProxyInputSchema.safeParse({ host: "", port: 1080 });
    expect(result.success).toBe(false);
  });

  it.each([0, -1, 65536, 70000])("rejects out-of-range port %i", (badPort) => {
    const result = AccountProxyInputSchema.safeParse({
      host: "proxy.example.com",
      port: badPort,
    });
    expect(result.success).toBe(false);
  });

  it("rejects non-integer port", () => {
    const result = AccountProxyInputSchema.safeParse({
      host: "proxy.example.com",
      port: 1080.5,
    });
    expect(result.success).toBe(false);
  });

  it("accepts explicit password clearing", () => {
    const parsed = AccountProxyInputSchema.parse({
      host: "proxy.example.com",
      port: 1080,
      password: null,
      clearPassword: true,
    });
    expect(parsed.password).toBeNull();
    expect(parsed.clearPassword).toBe(true);
  });
});

describe("AccountProxySummarySchema", () => {
  it("parses a full summary including last_validated_at", () => {
    const parsed = AccountProxySummarySchema.parse({
      host: "proxy.example.com",
      port: 1080,
      username: proxyUserFixture,
      hasPassword: true,
      remoteDns: false,
      label: "house-1",
      lastValidatedAt: "2026-05-23T12:00:00+00:00",
    });
    expect(parsed.host).toBe("proxy.example.com");
    expect(parsed.hasPassword).toBe(true);
    expect(parsed.remoteDns).toBe(false);
    expect(parsed.label).toBe("house-1");
  });

  it("never decodes a password field even if the server accidentally sent one", () => {
    // Zod ignores unknown keys by default; the contract is that the summary
    // schema does not declare a `password` field, so it can never be parsed
    // into the typed object the UI consumes.
    const raw: unknown = {
      host: "proxy.example.com",
      port: 1080,
      hasPassword: true,
      password: "leaked-from-server",
    };
    const parsed = AccountProxySummarySchema.parse(raw);
    expect("password" in parsed).toBe(false);
  });
});

describe("probe error helpers", () => {
  function makeProbeError(reason: string | null, message = "probe failed"): ApiError {
    const payload = reason
      ? { error: { code: "proxy_probe_failed", message, reason } }
      : { error: { code: "proxy_probe_failed", message } };
    return new ApiError({
      status: 422,
      code: "proxy_probe_failed",
      message,
      details: payload.error,
      payload,
    });
  }

  it("returns the typed reason for probe failures", () => {
    expect(probeReasonFromError(makeProbeError("proxy_auth"))).toBe("proxy_auth");
    expect(probeReasonFromError(makeProbeError("timeout"))).toBe("timeout");
  });

  it("returns null for unrelated ApiErrors", () => {
    const err = new ApiError({
      status: 404,
      code: "account_not_found",
      message: "missing",
    });
    expect(probeReasonFromError(err)).toBeNull();
  });

  it("returns null for non-ApiError values", () => {
    expect(probeReasonFromError(new Error("plain"))).toBeNull();
    expect(probeReasonFromError("oops")).toBeNull();
  });

  it.each([
    ["proxy_connect", "Could not reach the proxy"],
    ["proxy_auth", "rejected the username"],
    ["tls", "TLS handshake"],
    ["upstream_status", "Upstream rejected"],
    ["timeout", "probe timed out"],
  ])("formats %s as a friendly message", (reason, fragment) => {
    const message = formatProbeError(makeProbeError(reason));
    expect(message.toLowerCase()).toContain(fragment.toLowerCase());
  });

  it("falls back to the server message for unknown reasons", () => {
    const message = formatProbeError(makeProbeError("brand_new", "the proxy melted"));
    expect(message).toBe("the proxy melted");
  });

  it("falls back to a generic message for non-ApiError values", () => {
    expect(formatProbeError("oops")).toBe("Failed to validate proxy");
    expect(formatProbeError(new Error("boom"))).toBe("boom");
  });
});
