import { afterEach, describe, expect, it, vi } from "vitest";

import { exportAccountBundle } from "@/features/accounts/api";
import { setUnauthorizedHandler } from "@/lib/api-client";

describe("accounts api", () => {
  afterEach(() => {
    setUnauthorizedHandler(null);
    vi.unstubAllGlobals();
  });

  it("triggers the global unauthorized handler when bundle export returns 401", async () => {
    const unauthorizedHandler = vi.fn();
    setUnauthorizedHandler(unauthorizedHandler);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ error: { code: "unauthorized", message: "Authentication required" } }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(exportAccountBundle([], "bundle-passphrase")).rejects.toThrow("Authentication required");
    expect(unauthorizedHandler).toHaveBeenCalledOnce();
  });
});
