import { describe, expect, it } from "vitest";

import {
  externalProviders,
  isLocalLoginRequested,
  isSignInFailureRequested,
  localFormDisclosure,
  LOCAL_LOGIN_URL,
} from "@/features/auth/local-login";
import type { LoginProvider } from "@/features/auth/schemas";

const password: LoginProvider = { kind: "password", providerKey: "default", label: "Password", loginUrl: null };
const proxy: LoginProvider = {
  kind: "trusted_header",
  providerKey: "default",
  label: "Reverse proxy",
  loginUrl: null,
};

describe("isLocalLoginRequested", () => {
  it("recognises the break-glass query, with or without the leading question mark", () => {
    expect(isLocalLoginRequested("?local=1")).toBe(true);
    expect(isLocalLoginRequested("local=1")).toBe(true);
    expect(isLocalLoginRequested("?foo=bar&local=1")).toBe(true);
  });

  it("is false for anything else", () => {
    expect(isLocalLoginRequested("")).toBe(false);
    expect(isLocalLoginRequested("?local=0")).toBe(false);
    expect(isLocalLoginRequested("?local=true")).toBe(false);
    expect(isLocalLoginRequested("?locale=1")).toBe(false);
  });

  it("matches the URL the login-policy card hands out", () => {
    expect(isLocalLoginRequested(LOCAL_LOGIN_URL.slice(LOCAL_LOGIN_URL.indexOf("?")))).toBe(true);
  });
});

describe("isSignInFailureRequested", () => {
  it("recognises the marker the server redirects a failed company sign-in to", () => {
    expect(isSignInFailureRequested("?sso=failed")).toBe(true);
    expect(isSignInFailureRequested("sso=failed")).toBe(true);
    expect(isSignInFailureRequested("?local=1&sso=failed")).toBe(true);
  });

  it("is false for anything else, including a value that only looks like it", () => {
    expect(isSignInFailureRequested("")).toBe(false);
    expect(isSignInFailureRequested("?sso=1")).toBe(false);
    expect(isSignInFailureRequested("?sso=failure")).toBe(false);
    expect(isSignInFailureRequested("?ssofailed=1")).toBe(false);
  });

  it("matches the destination the backend names, and the two questions stay separate", () => {
    // `OIDC_FAILURE_PATH` in app/modules/dashboard_auth/oidc_api.py.
    expect(isSignInFailureRequested("?sso=failed")).toBe(true);
    expect(isLocalLoginRequested("?sso=failed")).toBe(false);
  });
});

describe("localFormDisclosure", () => {
  it("shows the form on an install that has not restricted anything", () => {
    expect(localFormDisclosure("enabled")).toBe("shown");
    expect(localFormDisclosure("enabled", { localRequested: true })).toBe("shown");
  });

  it("collapses it behind a link for admins_only, because it still works for some accounts", () => {
    expect(localFormDisclosure("admins_only")).toBe("collapsed");
  });

  it("hides it entirely for break_glass_only", () => {
    expect(localFormDisclosure("break_glass_only")).toBe("hidden");
  });

  it("always shows it when the URL asks for it", () => {
    expect(localFormDisclosure("admins_only", { localRequested: true })).toBe("shown");
    expect(localFormDisclosure("break_glass_only", { localRequested: true })).toBe("shown");
  });
});

describe("externalProviders", () => {
  it("drops the local password and keeps the rest in order", () => {
    expect(externalProviders([password, proxy])).toEqual([proxy]);
    expect(externalProviders([password])).toEqual([]);
    expect(externalProviders([])).toEqual([]);
  });
});
