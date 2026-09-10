import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { ApiError, put, setStepUpHandlers } from "@/lib/api-client";

const okSchema = z.object({ status: z.string() });

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const stepUpRequired = {
  error: {
    code: "step_up_required",
    message: "Confirm your identity to continue",
    param: "security:write",
    details: { methods: ["password", "totp"] },
  },
};

describe("api-client step-up", () => {
  afterEach(() => {
    setStepUpHandlers(null);
    vi.unstubAllGlobals();
  });

  it("runs the step-up flow once and replays the request when it succeeds", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(stepUpRequired, 403))
      .mockResolvedValueOnce(jsonResponse({ status: "ok" }, 200));
    vi.stubGlobal("fetch", fetchMock);
    const onRequired = vi.fn().mockResolvedValue(true);
    setStepUpHandlers({ onRequired, onUnavailable: vi.fn() });

    const result = await put("/api/settings", okSchema, { body: { guestAccessEnabled: true } });

    expect(result).toEqual({ status: "ok" });
    expect(onRequired).toHaveBeenCalledWith(["password", "totp"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    // The replay is the very same request.
    expect(fetchMock.mock.calls[1][0]).toBe("/api/settings");
    expect(fetchMock.mock.calls[1][1].body).toBe(JSON.stringify({ guestAccessEnabled: true }));
  });

  it("surfaces the 403 and does not retry when the person cancels", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(stepUpRequired, 403));
    vi.stubGlobal("fetch", fetchMock);
    setStepUpHandlers({ onRequired: vi.fn().mockResolvedValue(false), onUnavailable: vi.fn() });

    await expect(put("/api/settings", okSchema, { body: {} })).rejects.toMatchObject({
      code: "step_up_required",
      status: 403,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("never loops: a second step_up_required after a successful step-up is thrown", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(stepUpRequired, 403)));
    vi.stubGlobal("fetch", fetchMock);
    const onRequired = vi.fn().mockResolvedValue(true);
    setStepUpHandlers({ onRequired, onUnavailable: vi.fn() });

    await expect(put("/api/settings", okSchema, { body: {} })).rejects.toBeInstanceOf(ApiError);
    expect(onRequired).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("reports step_up_unavailable through the handler and throws", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { error: { code: "step_up_unavailable", message: "Set up two-factor authentication or a local password" } },
          403,
        ),
      ),
    );
    const onUnavailable = vi.fn();
    const onRequired = vi.fn();
    setStepUpHandlers({ onRequired, onUnavailable });

    await expect(put("/api/settings", okSchema, { body: {} })).rejects.toMatchObject({ code: "step_up_unavailable" });
    expect(onUnavailable).toHaveBeenCalledWith("Set up two-factor authentication or a local password");
    expect(onRequired).not.toHaveBeenCalled();
  });

  it("leaves other 403s alone", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ error: { code: "permission_required", message: "no" } }, 403)),
    );
    const onRequired = vi.fn();
    setStepUpHandlers({ onRequired, onUnavailable: vi.fn() });

    await expect(put("/api/settings", okSchema, { body: {} })).rejects.toMatchObject({ code: "permission_required" });
    expect(onRequired).not.toHaveBeenCalled();
  });
});
