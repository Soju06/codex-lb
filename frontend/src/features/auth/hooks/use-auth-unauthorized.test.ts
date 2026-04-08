import { beforeEach, describe, expect, it, vi } from "vitest";

let registeredUnauthorizedHandler: (() => void) | null = null;

vi.mock("@/features/auth/api", () => ({
  getAuthSession: vi.fn(),
  loginPassword: vi.fn(),
  logout: vi.fn(),
  verifyTotp: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  setUnauthorizedHandler: (handler: (() => void) | null) => {
    registeredUnauthorizedHandler = handler;
  },
}));

describe("useAuthStore unauthorized handler", () => {
  beforeEach(() => {
    vi.resetModules();
    registeredUnauthorizedHandler = null;
  });

  it("preserves bootstrap state on 401 handling", async () => {
    const { useAuthStore } = await import("@/features/auth/hooks/use-auth");

    useAuthStore.setState({
      authenticated: true,
      initialized: true,
      bootstrapRequired: true,
      bootstrapTokenConfigured: true,
      error: "boom",
    });

    expect(registeredUnauthorizedHandler).not.toBeNull();
    registeredUnauthorizedHandler?.();

    const next = useAuthStore.getState();
    expect(next.authenticated).toBe(false);
    expect(next.initialized).toBe(true);
    expect(next.error).toBeNull();
    expect(next.bootstrapRequired).toBe(true);
    expect(next.bootstrapTokenConfigured).toBe(true);
  });
});
