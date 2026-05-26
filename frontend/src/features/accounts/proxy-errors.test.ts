import { describe, expect, it } from "vitest";

import { formatProbeError, probeReasonFromError } from "@/features/accounts/proxy-errors";
import { ApiError } from "@/lib/api-client";

function makeApiError(code: string, payload: Record<string, unknown>): ApiError {
  return new ApiError({
    message: "api error",
    status: 422,
    code,
    payload,
  });
}

describe("formatProbeError", () => {
  it.each([
    ["proxy_connect", "Could not reach the proxy"],
    ["proxy_auth", "rejected the username or password"],
    ["tls", "TLS handshake"],
    ["upstream_status", "Upstream rejected"],
    ["timeout", "probe timed out"],
  ])("renders %s reason", (reason, fragment) => {
    const error = makeApiError("proxy_probe_failed", { error: { reason } });
    const message = formatProbeError(error);
    expect(message.toLowerCase()).toContain(fragment.toLowerCase());
  });

  it("falls back to the API error message for unknown codes", () => {
    const error = makeApiError("some_unrelated_error", {});
    const message = formatProbeError(error);
    expect(message).toBe("api error");
  });

  it("falls back to a generic message for non-API errors", () => {
    const message = formatProbeError(new Error("wat"));
    expect(message).toBe("wat");
  });
});

describe("probeReasonFromError", () => {
  it("extracts the reason field from a proxy_probe_failed envelope", () => {
    const error = makeApiError("proxy_probe_failed", {
      error: { reason: "tls" },
    });
    expect(probeReasonFromError(error)).toBe("tls");
  });

  it("returns null for non-probe codes", () => {
    expect(probeReasonFromError(makeApiError("validation_error", {}))).toBeNull();
  });

  it("returns null for non-ApiError exceptions", () => {
    expect(probeReasonFromError(new Error("wat"))).toBeNull();
  });
});
