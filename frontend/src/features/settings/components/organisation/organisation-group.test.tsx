import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { OrganisationSettingsGroup } from "@/features/settings/components/organisation/organisation-group";
import { renderAt, signInAsTeamAdmin } from "@/test/access-test-utils";
import {
  ADMIN_PERMISSIONS,
  createAccessSummary,
  createAuthProvider,
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
});
