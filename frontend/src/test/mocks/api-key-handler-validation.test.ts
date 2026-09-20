import { describe, expect, it } from "vitest";

describe("API-key mock validation", () => {
  it("preserves the factory name when create omits it", async () => {
    const response = await fetch(new URL("/api/api-keys/", window.location.href), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({}),
    });

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({ name: "Default key" });
  });

  it.each([
    ["create", "POST", "/api/api-keys/", { name: "Invalid share", usageSharePercent: 0 }],
    ["update", "PATCH", "/api/api-keys/key_1", { usageSharePercent: 0 }],
  ])("returns 422 for an invalid %s payload", async (_name, method, path, body) => {
    const response = await fetch(new URL(path, window.location.href), {
      method,
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });

    expect(response.status).toBe(422);
    await expect(response.json()).resolves.toMatchObject({
      error: { code: "validation_error" },
    });
  });
});
