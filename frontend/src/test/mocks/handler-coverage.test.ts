import { describe, expect, it } from "vitest";

import { handlers } from "@/test/mocks/handlers";

/**
 * Structural test that ensures the MSW handler set covers every API endpoint
 * consumed by the frontend. When a new endpoint is added to an api.ts file,
 * add the corresponding method+path here so this test forces the mock handler
 * to be created at the same time.
 */

function extractHandlerPaths(): string[] {
  return handlers.map((handler) => {
    const { method, path } = handler.info;
    // Normalize: MSW stores method in uppercase, path as the string literal
    return `${String(method).toUpperCase()} ${String(path)}`;
  });
}

// All API endpoints consumed by the frontend (method + MSW path pattern).
// Parameterized segments use MSW `:param` syntax.
const EXPECTED_ENDPOINTS = [
  // health
  "GET /health",
  // dashboard
  "GET /api/dashboard/overview",
  "GET /api/request-logs",
  "GET /api/request-logs/options",
  // accounts
  "GET /api/accounts",
  "POST /api/accounts/import",
  "POST /api/accounts/:accountId/pause",
  "POST /api/accounts/:accountId/reactivate",
  "GET /api/accounts/:accountId/trends",
  "DELETE /api/accounts/:accountId",
  // oauth
  "POST /api/oauth/start",
  "GET /api/oauth/status",
  "POST /api/oauth/complete",
  // auth
  "GET /api/dashboard-auth/session",
  "POST /api/dashboard-auth/password/setup",
  "POST /api/dashboard-auth/password/login",
  "POST /api/dashboard-auth/password/change",
  "DELETE /api/dashboard-auth/password",
  "POST /api/dashboard-auth/totp/setup/start",
  "POST /api/dashboard-auth/totp/setup/confirm",
  "POST /api/dashboard-auth/totp/verify",
  "POST /api/dashboard-auth/totp/disable",
  "POST /api/dashboard-auth/logout",
  // settings
  "GET /api/settings",
  "PUT /api/settings",
  // firewall
  "GET /api/firewall/ips",
  "POST /api/firewall/ips",
  "DELETE /api/firewall/ips/:ipAddress",
  // models
  "GET /api/models",
  // api-keys
  "GET /api/api-keys/",
  "POST /api/api-keys/",
  "PATCH /api/api-keys/:keyId",
  "DELETE /api/api-keys/:keyId",
  "POST /api/api-keys/:keyId/regenerate",
];

describe("MSW handler coverage", () => {
  it("covers all expected API endpoints", () => {
    const actual = new Set(extractHandlerPaths());
    const missing = EXPECTED_ENDPOINTS.filter((ep) => !actual.has(ep));
    expect(missing, "Missing MSW handlers for these endpoints").toEqual([]);
  });

  it("has no unexpected handlers outside the expected set", () => {
    const expected = new Set(EXPECTED_ENDPOINTS);
    const actual = extractHandlerPaths();
    const extra = actual.filter((ep) => !expected.has(ep));
    expect(extra, "Unexpected MSW handlers — add them to EXPECTED_ENDPOINTS").toEqual([]);
  });
});
