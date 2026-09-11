import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { AccessPeopleTab } from "@/features/settings/components/access/access-people-tab";
import { renderAt, signInAsTeamAdmin } from "@/test/access-test-utils";
import {
  OPERATOR_PERMISSIONS,
  PRESET_ROLE_IDS,
  createDashboardSettings,
  createDashboardUser,
  createDefaultDashboardUsers,
  createSessionUser,
} from "@/test/mocks/factories";
import { MOCK_ISSUED_INVITE_TOKEN } from "@/test/mocks/handlers";
import { server } from "@/test/mocks/server";

function conflict(code: string, status = 409) {
  return HttpResponse.json({ error: { code, message: code } }, { status });
}

function renderTab(overrides: Partial<Parameters<typeof AccessPeopleTab>[0]> = {}) {
  const onInvite = vi.fn();
  const onIssued = vi.fn();
  renderAt(<AccessPeopleTab onInvite={onInvite} onIssued={onIssued} {...overrides} />);
  return { onInvite, onIssued };
}

async function openRowMenu(user: ReturnType<typeof userEvent.setup>, username: string, name: string) {
  const row = await screen.findByTestId(`people-row-${username}`);
  await user.click(within(row).getByRole("button", { name: `Actions for ${name}` }));
  return screen.findByRole("menu");
}

function menuLabels(menu: HTMLElement) {
  return within(menu)
    .getAllByRole("menuitem")
    .map((item) => item.textContent);
}

describe("AccessPeopleTab", () => {
  beforeEach(() => {
    signInAsTeamAdmin();
  });

  it("lists everyone with role badge, status, last sign-in and sign-in method", async () => {
    renderTab();

    const admin = await screen.findByTestId("people-row-admin");
    expect(within(admin).getByText("you")).toBeInTheDocument();
    expect(within(admin).getByText("Admin")).toBeInTheDocument();
    expect(within(admin).getByText("Active")).toBeInTheDocument();
    expect(within(admin).getByText("Password")).toBeInTheDocument();
    expect(within(admin).getByRole("img", { name: "Two-factor on" })).toBeInTheDocument();

    const ops = screen.getByTestId("people-row-ops");
    expect(within(ops).getByText("Sarah Kim")).toBeInTheDocument();
    expect(within(ops).getByText("ops")).toBeInTheDocument();
    expect(within(ops).queryByRole("img", { name: "Two-factor on" })).not.toBeInTheDocument();

    const invited = screen.getByTestId("people-row-lee");
    expect(within(invited).getByText("Invited")).toBeInTheDocument();
    expect(within(invited).getByText(/Invite expires in \d+h/)).toBeInTheDocument();
    expect(within(invited).getByText("Never")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Pending invites (1)" })).toBeInTheDocument();
    expect(screen.queryByText("View full page")).not.toBeInTheDocument();
    expect(await screen.findByText("Two-factor is not required for everyone at sign-in.")).toBeInTheDocument();
  });

  it("reads the TOTP requirement from the configured policy, not the session flag", async () => {
    server.use(http.get("/api/settings", () => HttpResponse.json(createDashboardSettings({ totpRequiredOnLogin: true }))));
    useAuthStore.setState({ totpRequiredOnLogin: false });

    renderTab();

    expect(await screen.findByText("Two-factor is required for everyone at sign-in.")).toBeInTheDocument();
  });

  it("fails closed while the TOTP policy is unknown: no statement, no compat reset, retry re-requests", async () => {
    const user = userEvent.setup();
    let settingsRequests = 0;
    let fail = true;
    server.use(
      http.get("/api/settings", () => {
        settingsRequests += 1;
        return fail
          ? HttpResponse.json({ error: { code: "internal_error", message: "boom" } }, { status: 500 })
          : HttpResponse.json(createDashboardSettings({ totpRequiredOnLogin: true }));
      }),
    );
    signInAsTeamAdmin({ user: createSessionUser({ id: "user_ops", username: "ops" }) });
    renderTab();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load the sign-in requirements.");
    expect(screen.queryByText("Two-factor is not required for everyone at sign-in.")).not.toBeInTheDocument();
    expect(screen.queryByText("Two-factor is required for everyone at sign-in.")).not.toBeInTheDocument();
    expect(menuLabels(await openRowMenu(user, "admin", "admin"))).toEqual(["Log out everywhere"]);
    await user.keyboard("{Escape}");

    fail = false;
    const before = settingsRequests;
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Two-factor is required for everyone at sign-in.")).toBeInTheDocument();
    expect(settingsRequests).toBe(before + 1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  describe("Require two-factor for administrators", () => {
    const TOGGLE = { name: "Require two-factor for administrators" };

    it("renders the toggle with the enrolment hint for security:write and saves the flag", async () => {
      const user = userEvent.setup();
      let settings = createDashboardSettings({ adminsWithoutTotpCount: 2 });
      const puts: unknown[] = [];
      server.use(
        http.get("/api/settings", () => HttpResponse.json(settings)),
        http.put("/api/settings", async ({ request }) => {
          const body = (await request.json()) as Record<string, unknown>;
          puts.push(body);
          settings = createDashboardSettings({ ...settings, totpRequiredForAdminRole: body.totpRequiredForAdminRole as boolean });
          return HttpResponse.json(settings);
        }),
      );
      renderTab();

      const toggle = await screen.findByRole("switch", TOGGLE);
      expect(toggle).not.toBeChecked();
      expect(screen.getByText(/2 administrators will have to set up two-factor before they can continue\./)).toBeInTheDocument();
      // The global requirement line stays as it was, next to the new toggle.
      expect(screen.getByText("Two-factor is not required for everyone at sign-in.")).toBeInTheDocument();

      await user.click(toggle);

      await waitFor(() => expect(screen.getByRole("switch", TOGGLE)).toBeChecked());
      expect(puts).toHaveLength(1);
      expect(puts[0]).toMatchObject({ totpRequiredForAdminRole: true, totpRequiredOnLogin: false });
    });

    it("is not rendered for a manager without security:write", async () => {
      signInAsTeamAdmin({
        permissions: [...OPERATOR_PERMISSIONS, "users:manage:all"],
        user: createSessionUser({ id: "user_ops", username: "ops" }),
      });
      renderTab();

      expect(await screen.findByText("Two-factor is not required for everyone at sign-in.")).toBeInTheDocument();
      expect(screen.queryByRole("switch", TOGGLE)).not.toBeInTheDocument();
    });

    it("explains the enable guard inline when the acting admin has no two-factor of their own", async () => {
      const user = userEvent.setup();
      server.use(
        http.put("/api/settings", () =>
          HttpResponse.json(
            { error: { code: "invalid_totp_config", message: "Set up your own TOTP before requiring it at sign-in" } },
            { status: 400 },
          ),
        ),
      );
      renderTab();

      await user.click(await screen.findByRole("switch", TOGGLE));

      expect(await screen.findByRole("alert")).toHaveTextContent("Set up your own two-factor first.");
      expect(screen.getByRole("switch", TOGGLE)).not.toBeChecked();
    });

    it("shows other server refusals verbatim", async () => {
      const user = userEvent.setup();
      server.use(
        http.put("/api/settings", () =>
          HttpResponse.json({ error: { code: "settings_conflict", message: "Settings were modified" } }, { status: 409 }),
        ),
      );
      renderTab();

      await user.click(await screen.findByRole("switch", TOGGLE));

      expect(await screen.findByRole("alert")).toHaveTextContent("Settings were modified");
    });
  });

  it("treats a still-loading TOTP policy as unknown", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/settings", () => new Promise<never>(() => undefined)));
    signInAsTeamAdmin({ user: createSessionUser({ id: "user_ops", username: "ops" }) });
    renderTab();

    await screen.findByTestId("people-row-admin");
    expect(screen.getByTestId("sign-in-requirements-loading")).toBeInTheDocument();
    expect(screen.queryByText(/required at sign-in/)).not.toBeInTheDocument();
    expect(menuLabels(await openRowMenu(user, "admin", "admin"))).toEqual(["Log out everywhere"]);
  });

  it("marks SSO-only rows as awaiting sign-in and offers no link to copy", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/dashboard-users", () =>
        HttpResponse.json([
          ...createDefaultDashboardUsers(),
          createDashboardUser({
            id: "user_sso",
            username: "sso.person",
            role: { id: PRESET_ROLE_IDS.viewer, slug: "viewer", name: "Viewer", kind: "preset" },
            status: "invited",
            isBreakGlass: false,
            totpConfigured: false,
            hasPassword: false,
            lastLoginAt: null,
            pendingInvite: { expiresAt: null, ssoOnly: true },
          }),
        ]),
      ),
    );
    renderTab();

    const row = await screen.findByTestId("people-row-sso.person");
    expect(row).toHaveTextContent("Awaiting first sign-in through the proxy");
    expect(menuLabels(await openRowMenu(user, "sso.person", "sso.person"))).toEqual(["Revoke invite"]);
  });

  it("gates row actions: self, invited and the migrated admin row", async () => {
    const user = userEvent.setup();
    renderTab();

    // Self: nothing the server would refuse (role, status, delete, own TOTP reset).
    expect(menuLabels(await openRowMenu(user, "admin", "admin"))).toEqual(["Log out everywhere"]);
    await user.keyboard("{Escape}");

    expect(menuLabels(await openRowMenu(user, "ops", "Sarah Kim"))).toEqual([
      "Change role",
      "Disable",
      "Log out everywhere",
      "Delete",
    ]);
    await user.keyboard("{Escape}");

    expect(menuLabels(await openRowMenu(user, "lee", "lee"))).toEqual(["Copy new link", "Revoke invite"]);
    await user.keyboard("{Escape}");

    // Signed in as the operator: the migrated `admin` row keeps role, status and existence.
    signInAsTeamAdmin({ user: createSessionUser({ id: "user_ops", username: "ops" }) });
    expect(menuLabels(await openRowMenu(user, "admin", "admin"))).toEqual(["Reset two-factor", "Log out everywhere"]);
  });

  it("hides Reset two-factor on the migrated admin row while TOTP is required at sign-in", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/settings", () => HttpResponse.json(createDashboardSettings({ totpRequiredOnLogin: true }))));
    signInAsTeamAdmin({ user: createSessionUser({ id: "user_ops", username: "ops" }) });
    renderTab();

    await screen.findByText("Two-factor is required for everyone at sign-in.");
    expect(menuLabels(await openRowMenu(user, "admin", "admin"))).toEqual(["Log out everywhere"]);
  });

  it("signs the self row out everywhere through the store, not the admin endpoint", async () => {
    const user = userEvent.setup();
    const logoutEverywhere = vi.fn().mockResolvedValue(undefined);
    useAuthStore.setState({ logoutEverywhere });
    const adminCalls: string[] = [];
    server.events.on("request:start", ({ request }) => {
      if (request.url.includes("/revoke-sessions")) adminCalls.push(request.url);
    });
    renderTab();

    await user.click(within(await openRowMenu(user, "admin", "admin")).getByRole("menuitem", { name: "Log out everywhere" }));

    await waitFor(() => expect(logoutEverywhere).toHaveBeenCalledTimes(1));
    expect(adminCalls).toEqual([]);
  });

  it.each([
    ["last_admin_protected", "At least one active admin must remain."],
    ["insufficient_delegation", "You can only grant or act on roles within your own permissions."],
    ["compat_user_locked", "The migrated admin account cannot be changed in this release."],
    [
      "last_break_glass_protected",
      "This is the only emergency account that can still get in while password sign-in is restricted. Set up another one first.",
    ],
  ])("shows a %s refusal inline and clears it when the next action starts", async (code, message) => {
    const user = userEvent.setup();
    server.use(
      http.patch("/api/dashboard-users/:userId", () => conflict(code, code === "insufficient_delegation" ? 403 : 409)),
    );
    renderTab();

    await user.click(within(await openRowMenu(user, "ops", "Sarah Kim")).getByRole("menuitem", { name: "Disable" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByTestId("people-row-ops")).toBeInTheDocument();

    server.resetHandlers();
    await user.click(within(await openRowMenu(user, "ops", "Sarah Kim")).getByRole("menuitem", { name: "Log out everywhere" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("changes a role through the dialog, which reopens on the row's current role", async () => {
    const user = userEvent.setup();
    renderTab();

    await user.click(within(await openRowMenu(user, "ops", "Sarah Kim")).getByRole("menuitem", { name: "Change role" }));
    let dialog = await screen.findByRole("dialog", { name: "Change role" });
    expect(within(dialog).getByRole("combobox", { name: "Role" })).toHaveTextContent("Operator");
    await user.click(within(dialog).getByRole("combobox", { name: "Role" }));
    await user.click(await screen.findByRole("option", { name: "Viewer" }));
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await user.click(within(await openRowMenu(user, "ops", "Sarah Kim")).getByRole("menuitem", { name: "Change role" }));
    dialog = await screen.findByRole("dialog", { name: "Change role" });
    expect(within(dialog).getByRole("combobox", { name: "Role" })).toHaveTextContent("Operator");
    await user.click(within(dialog).getByRole("combobox", { name: "Role" }));
    await user.click(await screen.findByRole("option", { name: "Viewer" }));
    await user.click(within(dialog).getByRole("button", { name: "Save role" }));

    await waitFor(() => expect(within(screen.getByTestId("people-row-ops")).getByText("Viewer")).toBeInTheDocument());
  });

  describe("a role the company login manages", () => {
    const mapped = () =>
      createDashboardUser({
        id: "user_mapped",
        username: "kim",
        displayName: "Kim Park",
        roleSource: "mapping",
        isBreakGlass: false,
        totpConfigured: false,
        role: { id: PRESET_ROLE_IDS.viewer, slug: "viewer", name: "Viewer", kind: "preset" },
      });

    beforeEach(() => {
      server.use(
        http.get("/api/dashboard-users", () => HttpResponse.json([...createDefaultDashboardUsers(), mapped()])),
      );
    });

    it("marks the row and offers taking it over instead of a plain change", async () => {
      const user = userEvent.setup();
      renderTab();

      expect(within(await screen.findByTestId("people-row-kim")).getByText("From company login")).toBeInTheDocument();
      expect(menuLabels(await openRowMenu(user, "kim", "Kim Park"))).toContain("Take over and change");
    });

    it("warns, then sends force so the account stops following the rules", async () => {
      const user = userEvent.setup();
      const patched: unknown[] = [];
      server.use(
        http.patch("/api/dashboard-users/user_mapped", async ({ request }) => {
          patched.push(await request.json());
          return HttpResponse.json({ ...mapped(), roleSource: "manual" });
        }),
      );
      renderTab();

      await user.click(
        within(await openRowMenu(user, "kim", "Kim Park")).getByRole("menuitem", { name: "Take over and change" }),
      );
      const dialog = await screen.findByRole("dialog", { name: "Change role" });
      expect(dialog).toHaveTextContent("Changing it here takes the account over");

      await user.click(within(dialog).getByRole("combobox", { name: "Role" }));
      await user.click(await screen.findByRole("option", { name: "Operator" }));
      await user.click(within(dialog).getByRole("button", { name: "Take over and change" }));

      await waitFor(() =>
        expect(patched).toEqual([{ roleId: PRESET_ROLE_IDS.operator, force: true }]),
      );
    });

    it("explains a role_managed_externally refusal in its own words", async () => {
      const user = userEvent.setup();
      server.use(http.patch("/api/dashboard-users/user_mapped", () => conflict("role_managed_externally")));
      renderTab();

      await user.click(
        within(await openRowMenu(user, "kim", "Kim Park")).getByRole("menuitem", { name: "Take over and change" }),
      );
      const dialog = await screen.findByRole("dialog", { name: "Change role" });
      await user.click(within(dialog).getByRole("combobox", { name: "Role" }));
      await user.click(await screen.findByRole("option", { name: "Operator" }));
      await user.click(within(dialog).getByRole("button", { name: "Take over and change" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "The company login decides this role. Use \u201cTake over and change\u201d to set it by hand.",
      );
    });
  });

  it("enables a disabled account", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/dashboard-users", () =>
        HttpResponse.json([
          ...createDefaultDashboardUsers(),
          createDashboardUser({
            id: "user_paused",
            username: "paused",
            status: "disabled",
            isBreakGlass: false,
            totpConfigured: false,
            role: { id: PRESET_ROLE_IDS.viewer, slug: "viewer", name: "Viewer", kind: "preset" },
          }),
        ]),
      ),
    );
    const patched: unknown[] = [];
    server.use(
      http.patch("/api/dashboard-users/user_paused", async ({ request }) => {
        patched.push(await request.json());
        return HttpResponse.json(createDashboardUser({ id: "user_paused", username: "paused", status: "active" }));
      }),
    );
    renderTab();

    const menu = await openRowMenu(user, "paused", "paused");
    expect(menuLabels(menu)).toEqual(["Change role", "Enable", "Log out everywhere", "Delete"]);
    await user.click(within(menu).getByRole("menuitem", { name: "Enable" }));

    await waitFor(() => expect(patched).toEqual([{ status: "active" }]));
  });

  it("deletes after confirmation", async () => {
    const user = userEvent.setup();
    renderTab();

    await user.click(within(await openRowMenu(user, "ops", "Sarah Kim")).getByRole("menuitem", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Delete Sarah Kim?");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(screen.queryByTestId("people-row-ops")).not.toBeInTheDocument());
  });

  it("hands a new link to the owner from the invited row and revokes from the pending sheet", async () => {
    const user = userEvent.setup();
    const { onIssued } = renderTab();

    await user.click(within(await openRowMenu(user, "lee", "lee")).getByRole("menuitem", { name: "Copy new link" }));
    await waitFor(() => expect(onIssued).toHaveBeenCalledTimes(1));
    expect(onIssued.mock.calls[0][0]).toMatchObject({ invite: { token: MOCK_ISSUED_INVITE_TOKEN }, username: null });

    await user.click(screen.getByRole("button", { name: "Pending invites (1)" }));
    const sheet = await screen.findByRole("dialog", { name: "Pending invites" });
    expect(within(sheet).getByText("lee")).toBeInTheDocument();
    expect(sheet).toHaveTextContent(/Viewer · Expires in 1d/);
    await user.click(within(sheet).getByRole("button", { name: "Revoke invite" }));

    expect(await within(sheet).findByText("No pending invites.")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByTestId("people-row-lee")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /Pending invites/ })).not.toBeInTheDocument();
  });

  it("shows resend errors inside the pending sheet and drops rows whose invite is gone", async () => {
    const user = userEvent.setup();
    server.use(http.post("/api/dashboard-users/:userId/invite", () => conflict("invite_not_pending")));
    renderTab();

    await user.click(await screen.findByRole("button", { name: "Pending invites (1)" }));
    const sheet = await screen.findByRole("dialog", { name: "Pending invites" });
    await user.click(within(sheet).getByRole("button", { name: "Copy new link" }));

    expect(await within(sheet).findByRole("alert")).toHaveTextContent("This account has no pending invite.");
    // The tab's own banner stays quiet: the sheet owns this error.
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it("shows the read-only roles sheet with the five presets and no editing controls", async () => {
    const user = userEvent.setup();
    renderTab();

    await user.click(await screen.findByRole("button", { name: "View roles" }));
    const sheet = await screen.findByRole("dialog", { name: "Roles" });

    expect(sheet).toHaveTextContent("There are five built-in roles. They cannot be changed.");
    const slugs = within(sheet)
      .getAllByTestId(/^role-card-/)
      .map((card) => card.getAttribute("data-testid"));
    expect(slugs).toEqual(["role-card-admin", "role-card-operator", "role-card-member", "role-card-viewer", "role-card-guest"]);
    expect(within(within(sheet).getByTestId("role-card-member")).getByText("Coming later")).toBeInTheDocument();
    expect(within(within(sheet).getByTestId("role-card-guest")).getByText("Anonymous, cannot be assigned")).toBeInTheDocument();
    expect(within(within(sheet).getByTestId("role-card-admin")).getByText("Read the audit log.")).toBeInTheDocument();
    expect(within(within(sheet).getByTestId("role-card-member")).getAllByText(/\(own only\)/)).toHaveLength(3);
    expect(within(sheet).queryByRole("button", { name: /clone|edit|duplicate/i })).not.toBeInTheDocument();
  });

  it("offers the full page only above eight rows and routes the header actions", async () => {
    const user = userEvent.setup();
    const onOpenMySignIn = vi.fn();
    server.use(
      http.get("/api/dashboard-users", () =>
        HttpResponse.json(
          Array.from({ length: 9 }, (_, index) =>
            createDashboardUser({
              id: `user_${index}`,
              username: `person${index}`,
              role: { id: PRESET_ROLE_IDS.viewer, slug: "viewer", name: "Viewer", kind: "preset" },
            }),
          ),
        ),
      ),
    );
    const { onInvite } = renderTab({ onOpenMySignIn });

    expect(await screen.findByRole("link", { name: "View full page" })).toHaveAttribute("href", "/settings/access");
    await user.click(screen.getByRole("button", { name: "Change in My sign-in" }));
    expect(onOpenMySignIn).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Invite" }));
    expect(onInvite).toHaveBeenCalledTimes(1);
  });
});
