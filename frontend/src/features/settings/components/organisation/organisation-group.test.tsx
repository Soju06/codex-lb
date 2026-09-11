import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { ORGANISATION_LOGIN_POLICY_ID } from "@/features/settings/advanced-settings-deeplink";
import { OrganisationSettingsGroup } from "@/features/settings/components/organisation/organisation-group";
import { renderAt, signInAsTeamAdmin } from "@/test/access-test-utils";
import {
  ADMIN_PERMISSIONS,
  createAccessSummary,
  createAuthProvider,
  createDashboardSettings,
  createDashboardUser,
  createDefaultDashboardRoles,
  createRoleMapping,
  OPERATOR_PERMISSIONS,
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
      expect(screen.getAllByTestId("refused-sign-in-row")).toHaveLength(2);
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
      expect(screen.getByText("There is no company sign-in method to configure on this install yet.")).toBeInTheDocument();
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
});
