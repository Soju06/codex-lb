import { afterEach, describe, expect, it, vi } from "vitest";

import { loginGuest, loginPassword } from "@/features/auth/api";
import { ApiError, setUnauthorizedHandler } from "@/lib/api-client";

describe("auth api", () => {
  afterEach(() => {
    setUnauthorizedHandler(null);
    vi.unstubAllGlobals();
  });

  it("does not trigger the global unauthorized handler for password login failures", async () => {
    const unauthorizedHandler = vi.fn();
    setUnauthorizedHandler(unauthorizedHandler);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "invalid_credentials",
              message: "Invalid credentials",
            },
          }),
          {
            status: 401,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    await expect(loginPassword({ password: "wrong-password" })).rejects.toBeInstanceOf(ApiError);
    expect(unauthorizedHandler).not.toHaveBeenCalled();
  });

  it("does not trigger the global unauthorized handler for guest login failures", async () => {
    const unauthorizedHandler = vi.fn();
    setUnauthorizedHandler(unauthorizedHandler);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "invalid_credentials",
              message: "Invalid credentials",
            },
          }),
          {
            status: 401,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    await expect(loginGuest({ password: "wrong-password" })).rejects.toBeInstanceOf(ApiError);
    expect(unauthorizedHandler).not.toHaveBeenCalled();
  });
});
