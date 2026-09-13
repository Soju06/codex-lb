import type { LocalLoginPolicy, LoginProvider } from "@/features/auth/schemas";

// Pure rules for the local password form. They live outside the components so
// the policy can be unit-tested without a router and without rendering: the
// login form itself must keep working in a bare `render()`, so it receives the
// decision as a prop instead of reading the URL.

/** The public route that always offers the local form, whatever the policy says. */
export const LOCAL_LOGIN_ROUTE = "/login";

/** The URL an operator saves in a password manager before closing the door. */
export const LOCAL_LOGIN_URL = `${LOCAL_LOGIN_ROUTE}?local=1`;

/** Whether this URL asks for the local password form (`?local=1`). */
export function isLocalLoginRequested(search: string): boolean {
  const query = search.startsWith("?") ? search.slice(1) : search;
  return new URLSearchParams(query).get("local") === "1";
}

/**
 * How much of the local password form a screen shows:
 * - `shown`: the form, as every install has always had it.
 * - `collapsed`: a link that reveals the form in place — it still works for
 *   some accounts, so hiding it would strand them.
 * - `hidden`: nothing at all; the only way in is {@link LOCAL_LOGIN_URL}.
 */
export type LocalFormDisclosure = "shown" | "collapsed" | "hidden";

export function localFormDisclosure(
  policy: LocalLoginPolicy,
  { localRequested = false }: { localRequested?: boolean } = {},
): LocalFormDisclosure {
  if (policy === "enabled" || localRequested) {
    return "shown";
  }
  return policy === "admins_only" ? "collapsed" : "hidden";
}

/**
 * The sign-in methods that are not the local password, in the order the login
 * screen offers them: once a company login is active it is what almost
 * everybody uses, so it comes first and the password form follows.
 */
export function externalProviders(providers: readonly LoginProvider[]): LoginProvider[] {
  return providers.filter((provider) => provider.kind !== "password");
}
