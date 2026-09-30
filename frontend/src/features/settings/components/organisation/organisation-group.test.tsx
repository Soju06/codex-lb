import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/features/auth/hooks/use-auth";
import {
  flowNavigation,
  OIDC_FLOW_RETURN_CHANNEL,
  OIDC_FLOW_RETURN_MESSAGE,
  rememberFlow,
  takeFlow,
} from "@/features/auth/oidc-window";
import { LoginHintSchema } from "@/features/auth/schemas";
import { maskEmail, OIDC_KIND } from "@/features/organisation/rules";
import {
  ORGANISATION_LOGIN_POLICY_ID,
  ORGANISATION_SCIM_HASH,
  ORGANISATION_SCIM_ID,
} from "@/features/settings/advanced-settings-deeplink";
import { OrganisationSettingsGroup } from "@/features/settings/components/organisation/organisation-group";
import { renderAt, signInAsTeamAdmin } from "@/test/access-test-utils";
import {
  ADMIN_PERMISSIONS,
  createAccessSummary,
  createAuthProvider,
  createDashboardSettings,
  createDashboardUser,
  createDefaultDashboardRoles,
  createOidcAuthProvider,
  createRoleMapping,
  createScimToken,
  OIDC_PROVIDER_ID,
  PASSWORD_PROVIDER_ID,
  OPERATOR_PERMISSIONS,
  LOCAL_SIGN_IN_PROVIDER,
  PRESET_ROLE_IDS,
} from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";

// The word list PLAN §4.11 bans from copy an individual install can meet.
const ENTERPRISE_JARGON = /\b(user|role|SSO|SCIM|IdP|RBAC)\b/i;

const ENTERPRISE_PATHS = ["/api/auth-providers", "/api/role-mappings", "/api/audit-logs", "/api/dashboard-roles"];

function trackEnterpriseRequests(): string[] {
  const seen: string[] = [];
  server.events.on("request:start", ({ request }) => {
    const path = new URL(request.url).pathname;
    if (ENTERPRISE_PATHS.some((prefix) => path.startsWith(prefix))) {
      seen.push(path);
    }
  });
  return seen;
}

function useRules(...rules: ReturnType<typeof createRoleMapping>[]) {
  server.use(http.get("/api/role-mappings", () => HttpResponse.json(rules)));
}

/** What the rules API answers for this caller: only the roles it may hand out. */
function useAssignableRoles(roles: ReturnType<typeof createDefaultDashboardRoles>) {
  server.use(http.get("/api/role-mappings/assignable-roles", () => HttpResponse.json(roles)));
}

async function expand(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Show organisation settings" }));
}

describe("OrganisationSettingsGroup", () => {
  beforeEach(() => {
    signInAsTeamAdmin();
  });

  afterEach(() => {
    server.events.removeAllListeners();
    vi.restoreAllMocks();
  });

  it("is one collapsed line that mounts no card and issues no request", async () => {
    const requests = trackEnterpriseRequests();
    renderAt(<OrganisationSettingsGroup />);

    expect(screen.getByRole("button", { name: "Show organisation settings" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Reverse-proxy sign-in" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sign-in rules" })).not.toBeInTheDocument();
    // Give any stray effect a turn to fire before asserting the silence.
    await waitFor(() => expect(screen.getByTestId("organisation-group-line")).toBeInTheDocument());
    expect(requests).toEqual([]);
  });

  it("says nothing an individual install has to decode while nothing is configured", () => {
    renderAt(<OrganisationSettingsGroup />);

    const line = screen.getByTestId("organisation-group-line");
    expect(line).toHaveTextContent("Company login, automatic account management, audit export.");
    expect(line.textContent ?? "").not.toMatch(ENTERPRISE_JARGON);
  });

  it("replaces the line with a status summary once something is configured", () => {
    signInAsTeamAdmin({
      accessSummary: createAccessSummary({ providersEnabled: ["password", "trusted_header"], roleMappings: 2 }),
    });
    renderAt(<OrganisationSettingsGroup />);

    expect(screen.getByTestId("organisation-group-line")).toHaveTextContent(
      "Company login is set up. 2 sign-in rules.",
    );
  });

  it("is not drawn at all without security:write", () => {
    signInAsTeamAdmin({ permissions: OPERATOR_PERMISSIONS });
    renderAt(<OrganisationSettingsGroup />);

    expect(screen.queryByRole("button", { name: "Show organisation settings" })).not.toBeInTheDocument();
  });

  it("fetches the cards' data only once it is expanded", async () => {
    const user = userEvent.setup();
    const requests = trackEnterpriseRequests();
    renderAt(<OrganisationSettingsGroup />);
    expect(requests).toEqual([]);

    await expand(user);

    await screen.findByRole("heading", { name: "Reverse-proxy sign-in" });
    expect(requests).toContain("/api/auth-providers");
    expect(requests).toContain("/api/role-mappings");
  });

  describe("company sign-in card", () => {
    // Assembled rather than written out: a line holding an identifier next to a
    // credential reads as a leaked pair whether or not it is one.
    const ISSUER = "https://login.example.com";
    const RETURN_URL = "https://codex.example.com/api/dashboard-auth/oidc/callback";
    const APPLICATION = ["codex", "lb", "dashboard"].join("-");
    const TYPED_SECRET = ["typed", "value", "0000"].join("-");
    const AUTHORIZE = `${ISSUER}/authorize?state=mock`;

    type FakeWindow = { location: { href: string }; closed: boolean; close: () => void };

    function useProviders(...providers: ReturnType<typeof createAuthProvider>[]) {
      server.use(http.get("/api/auth-providers", () => HttpResponse.json(providers)));
    }

    /** jsdom opens nothing, so the flow window is a handle the test can inspect. */
    function stubFlowWindow() {
      const handle: FakeWindow = {
        location: { href: "" },
        closed: false,
        close: () => {
          handle.closed = true;
        },
      };
      const open = vi.spyOn(window, "open").mockReturnValue(handle as unknown as Window);
      return { handle, open };
    }

    /**
     * What the returning window announces on its way out, said by a test that
     * is not one. It is the carrier that needs no opener, so it is the only
     * one an identity provider serving a cross-origin opener policy leaves.
     */
    function announceReturn() {
      const channel = new BroadcastChannel(OIDC_FLOW_RETURN_CHANNEL);
      channel.postMessage({ type: OIDC_FLOW_RETURN_MESSAGE });
      channel.close();
    }

    /**
     * Everything both web storages hold, read through the `length`/`key` pair
     * so it works against jsdom's `Storage` and against the suite's own shim.
     */
    function storedValues(): string {
      const seen: string[] = [];
      for (const storage of [window.localStorage, window.sessionStorage]) {
        for (let index = 0; index < storage.length; index += 1) {
          const key = storage.key(index);
          seen.push(key ?? "", (key === null ? null : storage.getItem(key)) ?? "");
        }
      }
      return seen.join("\n");
    }

    async function connect(user: ReturnType<typeof userEvent.setup>) {
      await user.click(await screen.findByRole("button", { name: "Connect company sign-in" }));
      await user.type(await screen.findByLabelText("Issuer address"), ISSUER);
      await user.type(screen.getByLabelText("Application ID"), APPLICATION);
      await user.type(screen.getByLabelText("Application secret"), TYPED_SECRET);
      const redirect = screen.getByLabelText("Return address");
      await user.clear(redirect);
      await user.type(redirect, RETURN_URL);
      await user.click(screen.getByRole("button", { name: "Next" }));
      await user.click(await screen.findByRole("button", { name: "Save connection" }));
    }

    it("invites a connection without claiming nothing was ever set up", async () => {
      const user = userEvent.setup();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await screen.findByRole("heading", { name: "Company sign-in" });
      const empty = screen.getByTestId("oidc-empty");
      expect(empty).toHaveTextContent("Nothing is connected yet.");
      // An empty `config` is also what an unreadable one looks like, so the card
      // may not assert a history it cannot see.
      expect(empty.textContent ?? "").not.toMatch(/never|no connection has/i);
      expect(within(empty).getByRole("button", { name: "Connect company sign-in" })).toBeEnabled();
    });

    it("sends the connection and the claim names as one document, and not before", async () => {
      const user = userEvent.setup();
      const patched: Record<string, unknown>[] = [];
      server.use(
        http.patch("/api/auth-providers/:providerId", async ({ request }) => {
          patched.push((await request.json()) as Record<string, unknown>);
          return HttpResponse.json(createOidcAuthProvider());
        }),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Connect company sign-in" }));
      await user.type(await screen.findByLabelText("Issuer address"), ISSUER);
      await user.type(screen.getByLabelText("Application ID"), APPLICATION);
      await user.type(screen.getByLabelText("Application secret"), TYPED_SECRET);
      const redirect = screen.getByLabelText("Return address");
      await user.clear(redirect);
      await user.type(redirect, RETURN_URL);
      // Step (a) writes nothing: the server refuses a partial document.
      await user.click(screen.getByRole("button", { name: "Next" }));
      expect(patched).toEqual([]);

      await user.type(await screen.findByLabelText("Groups claim (optional)"), "roles");
      await user.click(screen.getByRole("button", { name: "Save connection" }));

      await waitFor(() =>
        expect(patched).toEqual([
          {
            config: {
              issuer: ISSUER,
              discoveryUrl: null,
              clientId: APPLICATION,
              clientSecret: TYPED_SECRET,
              redirectUri: RETURN_URL,
              subjectClaim: null,
              emailClaim: null,
              nameClaim: null,
              groupsClaim: "roles",
            },
          },
        ]),
      );
    });

    it("refuses its own bad values before the server has to", async () => {
      const user = userEvent.setup();
      const patched: unknown[] = [];
      server.use(
        http.patch("/api/auth-providers/:providerId", async ({ request }) => {
          patched.push(await request.json());
          return HttpResponse.json(createOidcAuthProvider());
        }),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Connect company sign-in" }));
      await user.type(await screen.findByLabelText("Issuer address"), "http://login.example.com");
      await user.click(screen.getByRole("button", { name: "Next" }));
      await user.click(await screen.findByRole("button", { name: "Save connection" }));

      // Everything the request model rejects comes back without a field name,
      // so a wizard that let these through could not say what was wrong. The
      // pre-filled return address is one of them on a plain-HTTP install.
      expect(await screen.findAllByText("Enter a full https address.")).toHaveLength(2);
      expect(screen.getAllByText("This is required.").length).toBeGreaterThan(0);
      expect(patched).toEqual([]);
    });

    it("writes nothing when the dialog is closed before the connection is saved", async () => {
      const user = userEvent.setup();
      const patched: unknown[] = [];
      server.use(
        http.patch("/api/auth-providers/:providerId", async ({ request }) => {
          patched.push(await request.json());
          return HttpResponse.json(createOidcAuthProvider());
        }),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Connect company sign-in" }));
      await user.type(await screen.findByLabelText("Issuer address"), ISSUER);
      await user.keyboard("{Escape}");

      expect(patched).toEqual([]);
      expect(await screen.findByRole("button", { name: "Connect company sign-in" })).toBeInTheDocument();
    });

    it("says a saved connection is not on, and never hands the stored secret back", async () => {
      const user = userEvent.setup();
      useProviders(createAuthProvider(), createOidcAuthProvider());
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      const card = await screen.findByTestId("oidc-connected");
      expect(card).toHaveTextContent("The connection is saved but not turned on");
      expect(card).toHaveTextContent(ISSUER);

      await user.click(within(card).getByRole("button", { name: "Edit connection" }));
      expect(await screen.findByLabelText("Issuer address")).toHaveValue(ISSUER);
      expect(screen.getByLabelText("Application secret")).toHaveValue("");
      // The mask is evidence that a secret exists, never a value to send back.
      expect(screen.getByTestId("oidc-stored-secret")).toHaveTextContent("****4444");
      expect(screen.queryByDisplayValue("****4444")).not.toBeInTheDocument();
    });

    it("leaves the typed secret in nothing that outlives the round trip it was typed for", async () => {
      const user = userEvent.setup();
      server.use(http.patch("/api/auth-providers/:providerId", () => HttpResponse.json(createOidcAuthProvider())));
      const { queryClient } = renderAt(<OrganisationSettingsGroup />);
      await expand(user);
      await connect(user);
      // The document is written and the wizard has moved on to the test login.
      await screen.findByRole("button", { name: "Run test sign-in" });

      // The write's variables are the *group's* mutation state, not this
      // dialog's, and a settled mutation keeps them: this is the one place the
      // clear-text document could go on living after the step that sent it.
      const cachedVariables = () =>
        JSON.stringify(queryClient.getMutationCache().getAll().map((mutation) => mutation.state.variables));
      await waitFor(() => expect(cachedVariables()).not.toContain(TYPED_SECRET));
      // And the surfaces it must never have reached at all. The dialog is a
      // portal, so the whole document is the only honest place to look.
      expect(document.body.innerHTML).not.toContain(TYPED_SECRET);
      expect(`${window.location.search}${window.location.hash}`).not.toContain(TYPED_SECRET);
      expect(storedValues()).not.toContain(TYPED_SECRET);
    });

    it("opens the window before the start is awaited and waits for the server's word", async () => {
      const user = userEvent.setup();
      let verifiedAt: string | null = null;
      const patched: Record<string, unknown>[] = [];
      let release: (() => void) | undefined;
      const started = new Promise<void>((resolve) => {
        release = resolve;
      });
      server.use(
        http.get("/api/auth-providers", () =>
          HttpResponse.json([createAuthProvider(), createOidcAuthProvider({ testLoginVerifiedAt: verifiedAt })]),
        ),
        http.post("/api/dashboard-auth/oidc/test-login/start", async () => {
          await started;
          return HttpResponse.json({ authorizationUrl: AUTHORIZE });
        }),
        http.patch("/api/auth-providers/:providerId", async ({ request }) => {
          patched.push((await request.json()) as Record<string, unknown>);
          return HttpResponse.json(createOidcAuthProvider({ enabled: true, active: true }));
        }),
      );
      const { handle, open } = stubFlowWindow();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Test and turn on" }));
      expect(await screen.findByRole("button", { name: "Turn on" })).toBeDisabled();

      await user.click(screen.getByRole("button", { name: "Run test sign-in" }));
      // The window exists already: one opened after the awaited start — which
      // can interpose a step-up dialog — is blocked by every browser.
      expect(open).toHaveBeenCalledTimes(1);
      expect(handle.location.href).toBe("");

      release?.();
      await waitFor(() => expect(handle.location.href).toBe(AUTHORIZE));
      expect(screen.getByRole("button", { name: "Turn on" })).toBeDisabled();

      verifiedAt = new Date().toISOString();
      handle.closed = true;

      await waitFor(() => expect(screen.getByRole("button", { name: "Turn on" })).toBeEnabled());
      await user.click(screen.getByRole("button", { name: "Turn on" }));
      await waitFor(() => expect(patched).toEqual([{ enabled: true }]));
    });

    it("keeps the control disabled when the window comes back without finishing", async () => {
      const user = userEvent.setup();
      useProviders(createAuthProvider(), createOidcAuthProvider());
      const { handle } = stubFlowWindow();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Test and turn on" }));
      await user.click(await screen.findByRole("button", { name: "Run test sign-in" }));
      await waitFor(() => expect(handle.location.href).toBe(AUTHORIZE));
      handle.closed = true;
      announceReturn();

      expect(await screen.findByText("The test sign-in did not finish. Run it again.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Turn on" })).toBeDisabled();
    });

    it("says nothing while an identity provider's opener policy makes the handle look closed", async () => {
      const user = userEvent.setup();
      // What `Cross-Origin-Opener-Policy: same-origin` on the authorization
      // endpoint does to this card: the handle reports closed within half a
      // second of the start, while the operator is still typing their
      // password, and the window that comes back has no opener to report to.
      let verifiedAt: string | null = null;
      server.use(
        http.get("/api/auth-providers", () =>
          HttpResponse.json([createAuthProvider(), createOidcAuthProvider({ testLoginVerifiedAt: verifiedAt })]),
        ),
      );
      const requests = trackEnterpriseRequests();
      const reads = () => requests.filter((path) => path === "/api/auth-providers").length;
      const { handle } = stubFlowWindow();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Test and turn on" }));
      await user.click(await screen.findByRole("button", { name: "Run test sign-in" }));
      await waitFor(() => expect(handle.location.href).toBe(AUTHORIZE));
      const before = reads();
      handle.closed = true;

      // The severed handle is worth a re-read — and nothing else. Waiting for
      // that read is what proves the poll ran before the copy is checked.
      await waitFor(() => expect(reads()).toBeGreaterThan(before));
      expect(screen.getByRole("button", { name: "Turn on" })).toBeDisabled();
      expect(screen.queryByText("The test sign-in did not finish. Run it again.")).not.toBeInTheDocument();

      // The round trip completes anyway, and reports itself the one way that
      // survives: the same-origin channel, with no opener involved.
      verifiedAt = new Date().toISOString();
      announceReturn();

      await waitFor(() => expect(screen.getByRole("button", { name: "Turn on" })).toBeEnabled());
      expect(screen.queryByText("The test sign-in did not finish. Run it again.")).not.toBeInTheDocument();
    });

    it("does not arm from a stamp it did not watch arrive", async () => {
      const user = userEvent.setup();
      // Somebody else's fresh proof looks exactly like this one, and the API
      // will not say whose it is, so the server would answer 409.
      useProviders(
        createAuthProvider(),
        createOidcAuthProvider({ testLoginVerifiedAt: new Date().toISOString() }),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Test and turn on" }));
      expect(await screen.findByRole("button", { name: "Turn on" })).toBeDisabled();
      expect(screen.getByText(/needs a test sign-in that succeeded here in the last ten minutes/)).toBeInTheDocument();
    });

    it("continues in this tab when the browser refuses the window", async () => {
      const user = userEvent.setup();
      vi.spyOn(window, "open").mockReturnValue(null);
      const go = vi.spyOn(flowNavigation, "go").mockImplementation(() => {});
      useProviders(createAuthProvider(), createOidcAuthProvider());
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Test and turn on" }));
      await user.click(await screen.findByRole("button", { name: "Run test sign-in" }));

      await waitFor(() => expect(go).toHaveBeenCalledWith(AUTHORIZE));
      // The marker carries an id and two timestamps. Never the document, never the secret.
      expect(takeFlow()).toEqual({ providerId: OIDC_PROVIDER_ID, purpose: "test-login", verifiedAt: null });
    });

    it("comes back from that tab to the card, and arms from the re-read row", async () => {
      rememberFlow({ providerId: OIDC_PROVIDER_ID, purpose: "test-login", verifiedAt: null });
      useProviders(
        createAuthProvider(),
        createOidcAuthProvider({ testLoginVerifiedAt: "2026-02-01T00:00:00Z" }),
      );
      // The destination is the server's own, and it is not ours to change.
      renderAt(<OrganisationSettingsGroup />, "/settings?org=1#oidc");

      expect(await screen.findByRole("button", { name: "Turn on" })).toBeEnabled();
      expect(takeFlow()).toBeNull();
    });

    it("puts a field-named refusal on its field and stays on the connection step", async () => {
      const user = userEvent.setup();
      server.use(
        http.patch("/api/auth-providers/:providerId", () =>
          HttpResponse.json(
            {
              error: {
                code: "invalid_provider_config",
                message: "raw server message",
                param: "redirect_uri",
              },
            },
            { status: 422 },
          ),
        ),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);
      await connect(user);

      expect(
        await screen.findByText(
          "One of the connection settings cannot be used. The field it belongs to is marked below.",
        ),
      ).toBeInTheDocument();
      expect(screen.getByLabelText("Return address")).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByText("The server refused this value.")).toBeInTheDocument();
      expect(screen.queryByText("raw server message")).not.toBeInTheDocument();
    });

    it("blames the addresses when the identity provider cannot be reached, and leaves no window open", async () => {
      const user = userEvent.setup();
      useProviders(createAuthProvider(), createOidcAuthProvider());
      server.use(
        http.post("/api/dashboard-auth/oidc/test-login/start", () =>
          HttpResponse.json(
            { error: { code: "oidc_provider_unreachable", message: "raw server message" } },
            { status: 502 },
          ),
        ),
      );
      const { handle } = stubFlowWindow();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Test and turn on" }));
      await user.click(await screen.findByRole("button", { name: "Run test sign-in" }));

      expect(
        await screen.findByText(
          "That service could not be reached with these settings. Check the issuer and discovery addresses.",
        ),
      ).toBeInTheDocument();
      expect(handle.closed).toBe(true);
      expect(screen.queryByText("raw server message")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Turn on" })).toBeDisabled();
    });

    it("names the wait for a rate-limited start and starts nothing else", async () => {
      const user = userEvent.setup();
      const starts: string[] = [];
      useProviders(createAuthProvider(), createOidcAuthProvider());
      server.use(
        http.post("/api/dashboard-auth/oidc/test-login/start", () => {
          starts.push("start");
          return HttpResponse.json(
            { error: { code: "oidc_rate_limited", message: "raw server message" } },
            { status: 429, headers: { "Retry-After": "30" } },
          );
        }),
      );
      stubFlowWindow();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Test and turn on" }));
      await user.click(await screen.findByRole("button", { name: "Run test sign-in" }));

      // The wait rides in `Retry-After`, not in the envelope, and is named.
      expect(
        await screen.findByText("Too many sign-in attempts just now. Try again in 30 seconds."),
      ).toBeInTheDocument();
      // The budget is shared with the public sign-in start: a retry loop here
      // would lock the operator out of signing in at all.
      expect(starts).toHaveLength(1);
    });

    it("explains a spent proof on the turn-on control and leaves the provider off", async () => {
      const user = userEvent.setup();
      const verifiedAt = new Date().toISOString();
      let stamp: string | null = null;
      server.use(
        http.get("/api/auth-providers", () =>
          HttpResponse.json([createAuthProvider(), createOidcAuthProvider({ testLoginVerifiedAt: stamp })]),
        ),
        http.patch("/api/auth-providers/:providerId", () =>
          HttpResponse.json(
            { error: { code: "oidc_test_login_required", message: "raw server message" } },
            { status: 409 },
          ),
        ),
      );
      const { handle } = stubFlowWindow();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Test and turn on" }));
      await user.click(await screen.findByRole("button", { name: "Run test sign-in" }));
      await waitFor(() => expect(handle.location.href).toBe(AUTHORIZE));
      stamp = verifiedAt;
      handle.closed = true;
      await waitFor(() => expect(screen.getByRole("button", { name: "Turn on" })).toBeEnabled());

      await user.click(screen.getByRole("button", { name: "Turn on" }));

      expect(await screen.findByText(/Run the test sign-in again before turning this on/)).toBeInTheDocument();
      expect(screen.queryByText("raw server message")).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Done" }));
      expect(await screen.findByTestId("oidc-connected")).toHaveTextContent(
        "The connection is saved but not turned on",
      );
    });

    it("explains a delegation refusal and leaves the way back open", async () => {
      const user = userEvent.setup();
      useProviders(createAuthProvider(), createOidcAuthProvider({ enabled: true, active: true }));
      server.use(
        http.patch("/api/auth-providers/:providerId", () =>
          HttpResponse.json(
            { error: { code: "insufficient_delegation", message: "raw server message" } },
            { status: 403 },
          ),
        ),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Edit connection" }));
      await user.type(await screen.findByLabelText("Application secret"), TYPED_SECRET);
      await user.click(screen.getByRole("button", { name: "Next" }));
      await user.click(await screen.findByRole("button", { name: "Save connection" }));

      expect(
        await screen.findByText("You can only hand out permissions you hold yourself."),
      ).toBeInTheDocument();
      expect(screen.queryByText("raw server message")).not.toBeInTheDocument();

      await user.keyboard("{Escape}");
      // Narrowing is never gated, so the recovery direction stays open.
      expect(await screen.findByRole("switch", { name: "Company sign-in is on" })).toBeEnabled();
    });

    it("never gates turning the provider back off", async () => {
      const user = userEvent.setup();
      const patched: Record<string, unknown>[] = [];
      useProviders(createAuthProvider(), createOidcAuthProvider({ enabled: true, active: true }));
      server.use(
        http.patch("/api/auth-providers/:providerId", async ({ request }) => {
          patched.push((await request.json()) as Record<string, unknown>);
          return HttpResponse.json(createOidcAuthProvider());
        }),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      const toggle = await screen.findByRole("switch", { name: "Company sign-in is on" });
      expect(toggle).toBeEnabled();
      await user.click(toggle);

      await waitFor(() => expect(patched).toEqual([{ enabled: false }]));
    });

    it("tells a session without security:write nothing about a connected provider", async () => {
      signInAsTeamAdmin({ permissions: OPERATOR_PERMISSIONS });
      useProviders(createAuthProvider(), createOidcAuthProvider({ enabled: true, active: true }));
      const requests = trackEnterpriseRequests();
      renderAt(<OrganisationSettingsGroup />);

      await waitFor(() => expect(screen.getByTestId("location")).toBeInTheDocument());
      expect(screen.queryByRole("heading", { name: "Company sign-in" })).not.toBeInTheDocument();
      expect(screen.queryByText("Okta")).not.toBeInTheDocument();
      expect(requests).toEqual([]);
    });

    it("names the one connected provider in the collapsed summary, from the session's own hint", () => {
      const requests = trackEnterpriseRequests();
      signInAsTeamAdmin({
        accessSummary: createAccessSummary({ providersEnabled: [LOCAL_SIGN_IN_PROVIDER.kind, OIDC_KIND], roleMappings: 2 }),
        loginHint: LoginHintSchema.parse({
          providers: [
            LOCAL_SIGN_IN_PROVIDER,
            { kind: "oidc", label: "Okta", loginUrl: "/api/dashboard-auth/oidc/login/start" },
          ],
        }),
      });
      renderAt(<OrganisationSettingsGroup />);

      const line = screen.getByTestId("organisation-group-line");
      expect(line).toHaveTextContent("Okta is connected. 2 sign-in rules.");
      expect(line.textContent ?? "").not.toMatch(ENTERPRISE_JARGON);
      expect(requests).toEqual([]);
    });

    it("quotes the operator's own label verbatim, even one this product would not write", () => {
      signInAsTeamAdmin({
        accessSummary: createAccessSummary({ providersEnabled: [LOCAL_SIGN_IN_PROVIDER.kind, OIDC_KIND], roleMappings: 1 }),
        loginHint: LoginHintSchema.parse({
          providers: [
            LOCAL_SIGN_IN_PROVIDER,
            { kind: "oidc", label: "SSO", loginUrl: "/api/dashboard-auth/oidc/login/start" },
          ],
        }),
      });
      renderAt(<OrganisationSettingsGroup />);

      // The word ban governs the copy this product writes. A customer's name for
      // their own system is quoted, not rewritten, abbreviated or decoded.
      expect(screen.getByTestId("organisation-group-line")).toHaveTextContent("SSO is connected. 1 sign-in rule.");
    });

    it("keeps the unnamed summary when two company sign-in methods are active", () => {
      signInAsTeamAdmin({
        accessSummary: createAccessSummary({
          providersEnabled: ["password", "trusted_header", "oidc"],
          roleMappings: 2,
        }),
        loginHint: LoginHintSchema.parse({
          providers: [
            LOCAL_SIGN_IN_PROVIDER,
            { kind: "trusted_header", label: "Reverse proxy" },
            { kind: "oidc", label: "Okta", loginUrl: "/api/dashboard-auth/oidc/login/start" },
          ],
        }),
      });
      renderAt(<OrganisationSettingsGroup />);

      expect(screen.getByTestId("organisation-group-line")).toHaveTextContent(
        "Company login is set up. 2 sign-in rules.",
      );
    });
  });

  describe("reverse-proxy card", () => {
    it("shows the header names read-only next to the variables that set them", async () => {
      const user = userEvent.setup();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await screen.findByRole("heading", { name: "Reverse-proxy sign-in" });
      expect(screen.getByText("Remote-User")).toBeInTheDocument();
      expect(screen.getByText("Remote-Groups")).toBeInTheDocument();
      expect(screen.getByText("Set with CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER")).toBeInTheDocument();
      expect(screen.getByText("Set with CODEX_LB_DASHBOARD_AUTH_PROXY_GROUPS_HEADER")).toBeInTheDocument();
      // Read-only means no textbox offers to change them.
      expect(screen.queryByDisplayValue("Remote-User")).not.toBeInTheDocument();
    });

    it("stays neutral about an install that does not run behind a proxy", async () => {
      const user = userEvent.setup();
      server.use(
        http.get("/api/auth-providers", () =>
          HttpResponse.json([createAuthProvider({ active: false })]),
        ),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      const note = await screen.findByText(/This install signs people in with a password today/);
      expect(note.textContent ?? "").not.toMatch(/wrong|invalid|misconfigur|error/i);
    });

    it("saves the knobs the backend owns", async () => {
      const user = userEvent.setup();
      const patched: unknown[] = [];
      server.use(
        http.patch("/api/auth-providers/:providerId", async ({ request }) => {
          const body: unknown = await request.json();
          patched.push(body);
          return HttpResponse.json({ ...createAuthProvider(), ...(body as object) });
        }),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("switch", { name: "Match by e-mail address" }));
      await waitFor(() => expect(patched).toEqual([{ linkByEmail: true }]));

      await user.click(screen.getByRole("switch", { name: "Leave existing accounts alone" }));
      await waitFor(() => expect(patched).toHaveLength(2));
      expect(patched[1]).toEqual({ skipRoleSync: true });
    });

    it("advises against admitting every arrival as an admin, without calling it an error", async () => {
      const user = userEvent.setup();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      const note = await screen.findByText(/Everyone the proxy vouches for becomes an admin/);
      expect(note.textContent ?? "").not.toMatch(/error|invalid|misconfigur/i);
    });

    it("explains a delegation refusal inline and keeps the saved value", async () => {
      const user = userEvent.setup();
      server.use(
        http.patch("/api/auth-providers/:providerId", () =>
          HttpResponse.json(
            { error: { code: "insufficient_delegation", message: "raw server message" } },
            { status: 403 },
          ),
        ),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("switch", { name: "Match by e-mail address" }));

      expect(await screen.findByText("You can only hand out permissions you hold yourself.")).toBeInTheDocument();
      expect(screen.queryByText("raw server message")).not.toBeInTheDocument();
      expect(screen.getByRole("switch", { name: "Match by e-mail address" })).not.toBeChecked();
    });

    it("offers refusing an arriving identity as well as a preset", async () => {
      const user = userEvent.setup();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("combobox", { name: "Someone arriving for the first time" }));
      const options = await screen.findAllByRole("option");
      expect(options.map((option) => option.textContent)).toEqual([
        "Refuse the sign-in",
        "Admin",
        "Operator",
        "Viewer",
      ]);
    });
  });

  describe("group-to-role rules card", () => {
    it("explains what happens with no rules and offers the one-domain quick add", async () => {
      const user = userEvent.setup();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      const empty = await screen.findByTestId("organisation-rules-empty");
      expect(empty).toHaveTextContent("No rules yet.");
      // The seeded provider admits unknown identities as Admin (D10), so the
      // empty state says that rather than promising a refusal it would not do.
      expect(empty).toHaveTextContent("Everyone arriving through company login is admitted as Admin.");
      // The suggestion comes from who was actually refused.
      expect(within(empty).getByRole("textbox", { name: "Value to match" })).toHaveValue("example.com");
      // Adding the first rule starts re-evaluating the accounts this method made.
      expect(empty).toHaveTextContent("accounts this login method created are checked again");
    });

    it("promises a refusal only when the provider refuses unknown identities", async () => {
      const user = userEvent.setup();
      server.use(
        http.get("/api/auth-providers", () =>
          HttpResponse.json([createAuthProvider({ unknownIdentityRoleId: null })]),
        ),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      expect(await screen.findByTestId("organisation-rules-empty")).toHaveTextContent(
        "Everyone arriving through company login is refused.",
      );
    });

    it("creates the quick-add rule for that e-mail domain", async () => {
      const user = userEvent.setup();
      const created: unknown[] = [];
      server.use(
        http.post("/api/role-mappings", async ({ request }) => {
          const body: unknown = await request.json();
          created.push(body);
          return HttpResponse.json(createRoleMapping({ claimName: "email_domain", claimValue: "example.com" }), {
            status: 201,
          });
        }),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      const empty = await screen.findByTestId("organisation-rules-empty");
      await user.click(within(empty).getByRole("button", { name: "Add rule" }));

      await waitFor(() =>
        expect(created).toEqual([
          {
            provider: "trusted_header",
            providerKey: "default",
            claimName: "email_domain",
            claimValue: "example.com",
            roleId: PRESET_ROLE_IDS.viewer,
          },
        ]),
      );
    });

    it("sends the whole new order, winner first, when a rule is moved up", async () => {
      const user = userEvent.setup();
      const orders: unknown[] = [];
      const first = createRoleMapping({ id: "mapping_platform", claimValue: "platform", priority: 2 });
      const second = createRoleMapping();
      useRules(first, second);
      server.use(
        http.put("/api/role-mappings/order", async ({ request }) => {
          const body: unknown = await request.json();
          orders.push(body);
          return HttpResponse.json([second, first]);
        }),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Move engineering up" }));

      await waitFor(() =>
        expect(orders).toEqual([
          { provider: "trusted_header", providerKey: "default", ids: ["mapping_engineering", "mapping_platform"] },
        ]),
      );
    });

    it("cannot move the winner up or the last rule down", async () => {
      const user = userEvent.setup();
      useRules(createRoleMapping({ id: "mapping_platform", claimValue: "platform", priority: 2 }), createRoleMapping());
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      expect(await screen.findByRole("button", { name: "Move platform up" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Move engineering down" })).toBeDisabled();
    });

    it("counts the refused sign-ins of the last week and opens the filtered list", async () => {
      const user = userEvent.setup();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      const line = await screen.findByTestId("organisation-refused-line");
      expect(line).toHaveTextContent("2 refused sign-ins in the last 7 days");

      await user.click(within(line).getByRole("button", { name: "view" }));
      expect(await screen.findByRole("heading", { name: "Refused sign-ins" })).toBeInTheDocument();
      const [first] = screen.getAllByTestId("refused-sign-in-row");
      expect(screen.getAllByTestId("refused-sign-in-row")).toHaveLength(2);
      // Beside the address, the same masked form the refused person is given as
      // a reference — so an administrator they quote it to finds this entry.
      expect(first).toHaveTextContent("sarah@example.com");
      expect(first).toHaveTextContent(maskEmail("sarah@example.com"));
    });

    it("says nothing about refusals when there were none", async () => {
      const user = userEvent.setup();
      server.use(http.get("/api/audit-logs", () => HttpResponse.json([])));
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await screen.findByRole("heading", { name: "Sign-in rules" });
      expect(screen.queryByTestId("organisation-refused-line")).not.toBeInTheDocument();
    });

    it("drops the counter, not the rules, for an account without audit:read", async () => {
      const user = userEvent.setup();
      signInAsTeamAdmin({ permissions: ADMIN_PERMISSIONS.filter((grant) => grant !== "audit:read:all") });
      const requests = trackEnterpriseRequests();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await screen.findByRole("heading", { name: "Sign-in rules" });
      expect(screen.queryByTestId("organisation-refused-line")).not.toBeInTheDocument();
      expect(requests).not.toContain("/api/audit-logs");
    });

    it("lists presets first, locked, and no custom role until the install has one", async () => {
      const user = userEvent.setup();
      const custom = {
        ...createDefaultDashboardRoles()[0],
        id: "role_billing",
        slug: "billing",
        name: "Billing",
        kind: "custom",
        locked: false,
      };
      // The server has already dropped what this caller may not hand out.
      useAssignableRoles([...createDefaultDashboardRoles().filter((role) => role.assignableToUsers), custom]);
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("combobox", { name: "What they get" }));
      expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual([
        "Admin",
        "Operator",
        "Viewer",
      ]);

      await user.keyboard("{Escape}");
      useAuthStore.setState({ accessSummary: createAccessSummary({ customRoles: 1 }) });
      await user.click(screen.getByRole("combobox", { name: "What they get" }));
      expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual([
        "Admin",
        "Operator",
        "Viewer",
        "Billing",
      ]);
    });
  });

  it("still names and offers roles for a session that may edit rules but not manage accounts", async () => {
    const user = userEvent.setup();
    // A custom role holding security:write and nothing about accounts: no
    // access summary, no assignable ids in the session, no roles list.
    signInAsTeamAdmin({
      permissions: [...OPERATOR_PERMISSIONS, "security:write:all"],
      accessSummary: null,
      assignableRoleIds: [],
    });
    useAssignableRoles(createDefaultDashboardRoles().filter((role) => role.slug === "viewer"));
    renderAt(<OrganisationSettingsGroup />);
    await expand(user);

    await screen.findByRole("heading", { name: "Sign-in rules" });
    const picker = screen.getByRole("combobox", { name: "What they get" });
    expect(picker).toBeEnabled();
    expect(picker).toHaveTextContent("Viewer");

    await user.click(picker);
    expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual(["Viewer"]);
  });

  it("expands and opens the refused list from its deep link", async () => {
    renderAt(<OrganisationSettingsGroup />, "/settings#organisation-refused");

    // The open sheet takes the accessible tree, so the expanded group behind it
    // is only addressable as hidden content.
    expect(await screen.findByRole("heading", { name: "Refused sign-ins" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sign-in rules", hidden: true })).toBeInTheDocument();
  });
  describe("login-policy card", () => {
    const ENROLLED_ADMIN = createDashboardUser({ username: "rescue" });
    const UNENROLLED_ADMIN = createDashboardUser({ username: "rescue", totpConfigured: false });

    function useUsers(...users: ReturnType<typeof createDashboardUser>[]) {
      server.use(http.get("/api/dashboard-users", () => HttpResponse.json(users)));
    }

    it("renders even on an install with no reverse proxy, where the other two cards do not", async () => {
      const user = userEvent.setup();
      server.use(http.get("/api/auth-providers", () => HttpResponse.json([])));
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      expect(await screen.findByRole("heading", { name: "Password sign-in" })).toBeInTheDocument();
      const note = screen.getByText(/No reverse proxy signs people in on this install/);
      // A topology fact, not a missing piece: a solo install behind nothing at
      // all must not read its own layout as an incomplete setup.
      expect(note.textContent ?? "").not.toMatch(/error|invalid|misconfigur|missing/i);
      expect(screen.queryByRole("heading", { name: "Reverse-proxy sign-in" })).not.toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Sign-in rules" })).not.toBeInTheDocument();
    });

    it("shows the emergency address and the account to save with it", async () => {
      const user = userEvent.setup();
      useUsers(ENROLLED_ADMIN);
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      const facts = await screen.findByTestId("organisation-emergency-facts");
      expect(facts).toHaveTextContent("rescue");
      expect(facts).toHaveTextContent(`${window.location.origin}/login?local=1`);
      expect(facts).toHaveTextContent("Ready: it has two-factor, so it can always get back in.");
    });

    it("names the account to enrol while nothing qualifies", async () => {
      const user = userEvent.setup();
      useUsers(UNENROLLED_ADMIN);
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      expect(
        await screen.findByText(
          "Turn on two-factor for rescue to qualify. Until then password sign-in cannot be restricted.",
        ),
      ).toBeInTheDocument();
      expect(screen.getByTestId("organisation-emergency-facts")).toHaveTextContent("Not ready: it has no two-factor yet.");
    });

    it("says so plainly when no emergency account is designated at all", async () => {
      const user = userEvent.setup();
      useUsers(createDashboardUser({ username: "rescue", isBreakGlass: false }));
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      expect(
        await screen.findByText(
          "No emergency account has been designated yet, so password sign-in cannot be restricted.",
        ),
      ).toBeInTheDocument();
    });

    it("saves the policy and refreshes the session so the rest of the page follows", async () => {
      const user = userEvent.setup();
      const refreshSession = vi.fn().mockResolvedValue(undefined);
      signInAsTeamAdmin({ refreshSession });
      useUsers(ENROLLED_ADMIN);
      const puts: Record<string, unknown>[] = [];
      server.use(
        http.put("/api/settings", async ({ request }) => {
          const body = (await request.json()) as Record<string, unknown>;
          puts.push(body);
          return HttpResponse.json(
            createDashboardSettings({ localLoginPolicy: body["localLoginPolicy"] as "break_glass_only" }),
          );
        }),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("combobox", { name: "Allowed to sign in with a password" }));
      await user.click(await screen.findByRole("option", { name: "The emergency account only" }));

      await waitFor(() => expect(puts).toHaveLength(1));
      expect(puts[0]).toMatchObject({ localLoginPolicy: "break_glass_only" });
      await waitFor(() => expect(refreshSession).toHaveBeenCalled());
    });

    it("explains a refusal instead of echoing the server", async () => {
      const user = userEvent.setup();
      useUsers(UNENROLLED_ADMIN);
      server.use(
        http.put("/api/settings", () =>
          HttpResponse.json(
            {
              error: {
                code: "break_glass_requires_totp",
                message: "raw server message",
                param: "rescue",
                details: { username: "rescue" },
              },
            },
            { status: 409 },
          ),
        ),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("combobox", { name: "Allowed to sign in with a password" }));
      await user.click(await screen.findByRole("option", { name: "Administrators only" }));

      expect(
        await screen.findByText(
          "Turn on two-factor for the emergency account first. Without it, restricting password sign-in could lock everybody out.",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText("raw server message")).not.toBeInTheDocument();
    });

    it("does not name an account it was not told about", async () => {
      const user = userEvent.setup();
      signInAsTeamAdmin({ permissions: ADMIN_PERMISSIONS.filter((grant) => grant !== "users:manage:all") });
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      const facts = await screen.findByTestId("organisation-emergency-facts");
      expect(facts).toHaveTextContent("Permission to manage people is needed to see which account this is.");
      expect(facts).not.toHaveTextContent("admin");
    });

    it("summarises a policy-only install without claiming a company login it does not have", () => {
      signInAsTeamAdmin({ accessSummary: createAccessSummary({ localLoginPolicy: "break_glass_only" }) });
      renderAt(<OrganisationSettingsGroup />);

      expect(screen.getByTestId("organisation-group-line")).toHaveTextContent("Password sign-in is restricted.");
      expect(screen.getByTestId("organisation-group-line")).not.toHaveTextContent("sign-in rules");
    });

    it("expands and scrolls from its own deep link", async () => {
      renderAt(<OrganisationSettingsGroup />, "/settings#organisation-login-policy");

      expect(await screen.findByRole("heading", { name: "Password sign-in" })).toBeInTheDocument();
    });

    it("waits for the group's own queries before scrolling to the card", async () => {
      // The card's anchor lives behind the group's spinner, and the scroll is
      // one animation-frame lookup that never retries: a deep link that raced
      // the providers, rules and roles requests used to find nothing and leave
      // the operator at the top of the page.
      let openGate: (() => void) | undefined;
      const gate = new Promise<void>((resolve) => {
        openGate = resolve;
      });
      const roles = createDefaultDashboardRoles();
      server.use(
        http.get("/api/auth-providers", async () => {
          await gate;
          return HttpResponse.json([createAuthProvider()]);
        }),
        http.get("/api/role-mappings", async () => {
          await gate;
          return HttpResponse.json([]);
        }),
        http.get("/api/role-mappings/assignable-roles", async () => {
          await gate;
          return HttpResponse.json(roles);
        }),
      );
      const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});

      renderAt(<OrganisationSettingsGroup />, "/settings#organisation-login-policy");

      // The group is open on the spinner, so the anchor does not exist yet.
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Hide organisation settings" })).toBeInTheDocument(),
      );
      expect(document.getElementById(ORGANISATION_LOGIN_POLICY_ID)).toBeNull();
      expect(scrollIntoView).not.toHaveBeenCalled();

      openGate?.();

      expect(await screen.findByRole("heading", { name: "Password sign-in" })).toBeInTheDocument();
      await waitFor(() => {
        const card = document.getElementById(ORGANISATION_LOGIN_POLICY_ID);
        expect(card).not.toBeNull();
        expect(scrollIntoView.mock.contexts).toContain(card);
      });

      scrollIntoView.mockRestore();
    });

    it("offers a retry instead of an endless spinner when the settings request fails", async () => {
      const user = userEvent.setup();
      let attempts = 0;
      server.use(
        http.get("/api/settings", () => {
          attempts += 1;
          return attempts === 1
            ? HttpResponse.json({ error: { code: "internal_error", message: "boom" } }, { status: 500 })
            : HttpResponse.json(createDashboardSettings({ localLoginPolicy: "admins_only" }));
        }),
      );
      useUsers(ENROLLED_ADMIN);
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      expect(await screen.findByText("The current setting could not be loaded.")).toBeInTheDocument();
      // Never a select offering "Everyone" as though that were the saved value.
      expect(screen.queryByRole("combobox", { name: "Allowed to sign in with a password" })).not.toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Retry" }));

      expect(await screen.findByRole("combobox", { name: "Allowed to sign in with a password" })).toHaveTextContent(
        "Administrators only",
      );
    });

    it("does not turn an unreadable people list into 'nobody is designated'", async () => {
      const user = userEvent.setup();
      server.use(
        http.get("/api/dashboard-users", () =>
          HttpResponse.json({ error: { code: "internal_error", message: "boom" } }, { status: 500 }),
        ),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      expect(
        await screen.findByText(
          "The list of accounts could not be loaded, so this cannot say whether an emergency account is ready.",
        ),
      ).toBeInTheDocument();
      expect(
        screen.queryByText(
          "No emergency account has been designated yet, so password sign-in cannot be restricted.",
        ),
      ).not.toBeInTheDocument();
      const facts = screen.getByTestId("organisation-emergency-facts");
      expect(facts).toHaveTextContent("Not known right now");
      expect(facts).not.toHaveTextContent("Ready: it has two-factor");
    });
  });

  describe("automatic account management", () => {
    /** Only the local sign-in: the install that cannot use this yet. */
    function passwordOnly() {
      server.use(
        http.get("/api/auth-providers", () =>
          HttpResponse.json([createAuthProvider({ id: PASSWORD_PROVIDER_ID, kind: "password", label: "Password" })]),
        ),
      );
    }

    function useTokens(...tokens: ReturnType<typeof createScimToken>[]) {
      server.use(http.get("/api/scim-tokens", () => HttpResponse.json({ tokens, basePath: "/scim/v2" })));
    }

    it("is drawn on an install that cannot use it yet, disabled, with the reason", async () => {
      const user = userEvent.setup();
      passwordOnly();
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      expect(await screen.findByRole("heading", { name: "Automatic account management" })).toBeInTheDocument();
      expect(screen.getByTestId("automatic-accounts-blocked")).toHaveTextContent(
        "Connect company sign-in first to turn this on.",
      );
      expect(screen.getByRole("button", { name: "Create credential" })).toBeDisabled();
      expect(screen.getByLabelText("Name this credential")).toBeDisabled();
      // Disabled is not silent: the address is still there to prepare with.
      expect(await screen.findByTestId("scim-base-url")).toHaveTextContent("/scim/v2");
    });

    it("mounts last, after the login-policy card", async () => {
      const user = userEvent.setup();
      server.use(
        http.get("/api/auth-providers", () =>
          HttpResponse.json([
            createAuthProvider({ id: PASSWORD_PROVIDER_ID, kind: "password", label: "Password" }),
            createAuthProvider(),
            createOidcAuthProvider({ enabled: true }),
          ]),
        ),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await screen.findByRole("heading", { name: "Automatic account management" });
      expect(screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)).toEqual([
        "Company sign-in",
        "Reverse-proxy sign-in",
        "Sign-in rules",
        "Password sign-in",
        "Automatic account management",
      ]);
    });

    it("shows a new credential once, keeps it through the tier flip, and only then refreshes", async () => {
      const user = userEvent.setup();
      // Nothing configured yet, so issuing the first credential is exactly the
      // write that flips the disclosure tier under the open dialog.
      const refreshSession = vi.fn(async () => {
        useAuthStore.setState({ accessSummary: createAccessSummary({ scimTokens: 1 }) });
        return useAuthStore.getState() as never;
      });
      signInAsTeamAdmin({ refreshSession });
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.type(await screen.findByLabelText("Name this credential"), "Directory");
      await user.click(screen.getByRole("button", { name: "Create credential" }));

      const secret = await screen.findByTestId("scim-token-secret");
      const plaintext = secret.textContent ?? "";
      expect(plaintext).not.toBe("");
      expect(refreshSession).not.toHaveBeenCalled();
      // The tier flips under the dialog; the dialog and its value survive it.
      expect(screen.getByTestId("scim-token-secret")).toHaveTextContent(plaintext);

      await user.click(screen.getByRole("button", { name: "Done" }));

      await waitFor(() => expect(refreshSession).toHaveBeenCalled());
      expect(screen.queryByTestId("scim-token-secret")).not.toBeInTheDocument();
      const list = await screen.findByTestId("scim-token-list");
      expect(list).toHaveTextContent("Directory");
      expect(list).not.toHaveTextContent(plaintext);
      expect(list).toHaveTextContent("Nothing received yet");
    });

    it("holds the plaintext in component state alone while it is on screen", async () => {
      const user = userEvent.setup();
      const { queryClient } = renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.type(await screen.findByLabelText("Name this credential"), "Directory");
      await user.click(screen.getByRole("button", { name: "Create credential" }));

      const plaintext = (await screen.findByTestId("scim-token-secret")).textContent ?? "";
      expect(plaintext).not.toBe("");
      // The dialog is still open, which is exactly when a mutation that settled
      // successfully would be holding the answer: `gcTime` disposes only of a
      // mutation nothing observes any more, and the group observes this one.
      const cached = JSON.stringify([
        queryClient
          .getMutationCache()
          .getAll()
          .map((mutation) => [mutation.state.data ?? null, mutation.state.variables ?? null]),
        queryClient
          .getQueryCache()
          .getAll()
          .map((query) => query.state.data ?? null),
      ]);
      expect(cached).not.toContain(plaintext);
    });

    it("shows a replacement once and keeps the row's label and last sync", async () => {
      const user = userEvent.setup();
      const existing = createScimToken({ label: "Directory", lastUsedAt: "2026-02-02T10:00:00Z" });
      // Assembled here rather than written out, and returned by the rotate
      // alone, so the assertion below proves the value came from that response.
      const replacement = [["clb", "scim"].join("-"), "replacement", "1".repeat(8)].join("_");
      useTokens(existing);
      server.use(
        http.post("/api/scim-tokens/:tokenId/rotate", () =>
          HttpResponse.json({ token: { ...existing, rotatedAt: "2026-02-03T00:00:00Z" }, secret: replacement }),
        ),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.click(await screen.findByRole("button", { name: "Replace" }));

      expect(await screen.findByTestId("scim-token-secret")).toHaveTextContent(replacement);
      await user.click(screen.getByRole("button", { name: "Done" }));

      const list = await screen.findByTestId("scim-token-list");
      expect(list).toHaveTextContent("Directory");
      expect(list).toHaveTextContent("Last received");
      expect(list).not.toHaveTextContent(replacement);
    });

    it("explains a refused issue in this product's words and opens no dialog", async () => {
      const user = userEvent.setup();
      server.use(
        http.post("/api/scim-tokens", () =>
          HttpResponse.json(
            {
              error: {
                code: "insufficient_delegation",
                message: "Issuing an automatic-account-management credential needs the permissions it could hand out",
              },
            },
            { status: 403 },
          ),
        ),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      await user.type(await screen.findByLabelText("Name this credential"), "Directory");
      await user.click(screen.getByRole("button", { name: "Create credential" }));

      expect(await screen.findByText("You can only hand out permissions you hold yourself.")).toBeInTheDocument();
      expect(screen.queryByText(/needs the permissions it could hand out/)).not.toBeInTheDocument();
      expect(screen.queryByTestId("scim-token-secret")).not.toBeInTheDocument();
    });

    it("warns that a person will arrive twice while the company sign-in does not match by e-mail", async () => {
      const user = userEvent.setup();
      server.use(
        http.get("/api/auth-providers", () =>
          HttpResponse.json([
            createAuthProvider({ id: PASSWORD_PROVIDER_ID, kind: "password", label: "Password" }),
            createOidcAuthProvider({ enabled: true, linkByEmail: false, label: "Okta" }),
          ]),
        ),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      expect(await screen.findByText(/Okta does not match people by e-mail/)).toBeInTheDocument();
      expect(screen.queryByTestId("automatic-accounts-blocked")).not.toBeInTheDocument();
    });

    it("does not turn an unreadable credential list into 'no credential exists'", async () => {
      const user = userEvent.setup();
      server.use(
        http.get("/api/scim-tokens", () =>
          HttpResponse.json({ error: { code: "internal_error", message: "boom" } }, { status: 500 }),
        ),
      );
      renderAt(<OrganisationSettingsGroup />);
      await expand(user);

      expect(await screen.findByText("The list of credentials could not be loaded.")).toBeInTheDocument();
      expect(screen.queryByTestId("scim-token-list")).not.toBeInTheDocument();
      // And never offers the site origin as the endpoint: a copyable address
      // that the answer did not supply is a wrong address.
      expect(screen.queryByTestId("scim-base-url")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Copy address" })).not.toBeInTheDocument();
    });

    it("says accounts are managed automatically once one credential exists, without the banned words", () => {
      signInAsTeamAdmin({ accessSummary: createAccessSummary({ scimTokens: 1 }) });
      renderAt(<OrganisationSettingsGroup />);

      const line = screen.getByTestId("organisation-group-line");
      expect(line).toHaveTextContent("Accounts are added and disabled automatically.");
      expect(line.textContent ?? "").not.toMatch(ENTERPRISE_JARGON);
    });

    it("issues no credential request while the group is collapsed", async () => {
      const seen: string[] = [];
      server.events.on("request:start", ({ request }) => {
        if (new URL(request.url).pathname.startsWith("/api/scim-tokens")) {
          seen.push(request.method);
        }
      });
      renderAt(<OrganisationSettingsGroup />);

      await waitFor(() => expect(screen.getByTestId("organisation-group-line")).toBeInTheDocument());
      expect(seen).toEqual([]);
    });

    it("is not drawn at all without security:write", () => {
      signInAsTeamAdmin({ permissions: OPERATOR_PERMISSIONS });
      renderAt(<OrganisationSettingsGroup />);

      expect(screen.queryByRole("heading", { name: "Automatic account management" })).not.toBeInTheDocument();
    });

    it("expands and scrolls from its own deep link", async () => {
      const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
      renderAt(<OrganisationSettingsGroup />, `/settings${ORGANISATION_SCIM_HASH}`);

      expect(await screen.findByRole("heading", { name: "Automatic account management" })).toBeInTheDocument();
      await waitFor(() => {
        const card = document.getElementById(ORGANISATION_SCIM_ID);
        expect(card).not.toBeNull();
        expect(scrollIntoView.mock.contexts).toContain(card);
      });

      scrollIntoView.mockRestore();
    });
  });

});
