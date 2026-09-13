import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { installAccessCacheEviction } from "@/features/auth/access-cache-eviction";
import { useAuthStore } from "@/features/auth/hooks/use-auth";

function seed(client: QueryClient) {
  client.setQueryData(["settings", "upstream-proxy"], { endpoints: [] });
  client.setQueryData(["api-keys", "list"], [{ id: "key_1" }]);
  client.setQueryData(["api-keys", "trends", "key_1"], { points: [] });
  client.setQueryData(["sticky-sessions", { limit: 50 }], { entries: [] });
  client.setQueryData(["accounts"], [{ accountId: "acc_primary" }]);
}

describe("installAccessCacheEviction", () => {
  beforeEach(() => {
    // The store boots least-privilege; start each case from a write-capable
    // session so the true -> false transition under test actually happens.
    useAuthStore.setState({ role: "admin", permissions: ["read", "write"], canWrite: true });
  });

  afterEach(() => {
    useAuthStore.setState({ role: "admin", permissions: ["read", "write"], canWrite: true });
  });

  it("removes write-only query data when write access is lost and leaves the rest", () => {
    const client = new QueryClient();
    seed(client);
    const unsubscribe = installAccessCacheEviction(client);

    useAuthStore.setState({ canWrite: false });

    expect(client.getQueryData(["settings", "upstream-proxy"])).toBeUndefined();
    expect(client.getQueryData(["api-keys", "list"])).toBeUndefined();
    expect(client.getQueryData(["api-keys", "trends", "key_1"])).toBeUndefined();
    expect(client.getQueryData(["sticky-sessions", { limit: 50 }])).toBeUndefined();
    expect(client.getQueryData(["accounts"])).toEqual([{ accountId: "acc_primary" }]);
    unsubscribe();
  });

  it("does not evict when write access is gained or unchanged", () => {
    const client = new QueryClient();
    useAuthStore.setState({ canWrite: false });
    const unsubscribe = installAccessCacheEviction(client);
    seed(client);

    useAuthStore.setState({ canWrite: true });
    useAuthStore.setState({ role: "admin" });

    expect(client.getQueryData(["settings", "upstream-proxy"])).toEqual({ endpoints: [] });
    expect(client.getQueryData(["api-keys", "list"])).toEqual([{ id: "key_1" }]);
    expect(client.getQueryData(["sticky-sessions", { limit: 50 }])).toEqual({ entries: [] });
    unsubscribe();
  });

  it("removes the people and roles lists when users:manage is lost or another account signs in", () => {
    const client = new QueryClient();
    useAuthStore.setState({ permissions: ["read", "write", "users:manage:all"], user: { id: "u1", username: "admin", displayName: null, role: { id: "r1", slug: "admin", name: "Admin", kind: "preset" } } });
    const unsubscribe = installAccessCacheEviction(client);
    const seedPeople = () => {
      client.setQueryData(["dashboard-users", "list"], [{ id: "u1" }]);
      client.setQueryData(["dashboard-roles", "list"], [{ id: "r1" }]);
    };

    seedPeople();
    useAuthStore.setState({ permissions: ["read", "write"] });
    expect(client.getQueryData(["dashboard-users", "list"])).toBeUndefined();
    expect(client.getQueryData(["dashboard-roles", "list"])).toBeUndefined();

    useAuthStore.setState({ permissions: ["read", "write", "users:manage:all"] });
    seedPeople();
    useAuthStore.setState({ user: { id: "u2", username: "ops", displayName: null, role: { id: "r2", slug: "operator", name: "Operator", kind: "preset" } } });
    expect(client.getQueryData(["dashboard-users", "list"])).toBeUndefined();

    seedPeople();
    useAuthStore.setState({ role: "admin" });
    expect(client.getQueryData(["dashboard-users", "list"])).toEqual([{ id: "u1" }]);
    unsubscribe();
    useAuthStore.setState({ user: null, permissions: ["read", "write"] });
  });

  it("stops watching after unsubscribe", () => {
    const client = new QueryClient();
    seed(client);
    installAccessCacheEviction(client)();

    useAuthStore.setState({ canWrite: false });

    expect(client.getQueryData(["settings", "upstream-proxy"])).toEqual({ endpoints: [] });
  });
});
