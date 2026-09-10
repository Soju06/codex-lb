import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { LoginHintSchema } from "@/features/auth/schemas";
import { InviteDialog } from "@/features/settings/components/access/invite-dialog";
import { renderAt, signInAsTeamAdmin } from "@/test/access-test-utils";
import { createAccessSummary } from "@/test/mocks/factories";
import { MOCK_ISSUED_INVITE_TOKEN } from "@/test/mocks/handlers";
import { server } from "@/test/mocks/server";

function renderDialog() {
  const onOpenChange = vi.fn();
  const onIssued = vi.fn();
  renderAt(<InviteDialog open onOpenChange={onOpenChange} onIssued={onIssued} />);
  return { onOpenChange, onIssued };
}

async function openDialog() {
  const dialog = await screen.findByRole("dialog", { name: "Invite a teammate" });
  await waitFor(() => expect(within(dialog).getByRole("combobox", { name: "Role" })).toHaveTextContent("Operator"));
  return dialog;
}

describe("InviteDialog", () => {
  beforeEach(() => {
    signInAsTeamAdmin({ accessSummary: createAccessSummary(), tier: "individual" });
  });

  it("preselects Operator, describes it, notes the first invite and hands the link to its owner without refreshing the session", async () => {
    const user = userEvent.setup();
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    useAuthStore.setState({ refreshSession });
    const { onOpenChange, onIssued } = renderDialog();

    const dialog = await openDialog();
    expect(dialog).toHaveTextContent("Accounts, all API keys and operational settings.");
    expect(within(dialog).getByTestId("first-invite-note")).toHaveTextContent(
      "After this invite, the sign-in screen asks for a username. Yours is admin.",
    );
    expect(within(dialog).queryByText(/Admins can do everything/)).not.toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Username"), "Sarah");
    await user.click(within(dialog).getByRole("button", { name: "Create invite link" }));

    await waitFor(() => expect(onIssued).toHaveBeenCalledTimes(1));
    expect(onIssued.mock.calls[0][0]).toMatchObject({ invite: { token: MOCK_ISSUED_INVITE_TOKEN }, username: "sarah" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
    // The owner refreshes after the link is acknowledged, not here.
    expect(refreshSession).not.toHaveBeenCalled();
  });

  it("warns when Admin is chosen and skips the first-invite note on an existing team", async () => {
    const user = userEvent.setup();
    signInAsTeamAdmin();
    renderDialog();

    const dialog = await openDialog();
    expect(within(dialog).queryByTestId("first-invite-note")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("combobox", { name: "Role" }));
    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(["Admin", "Operator", "Viewer"]);
    await user.click(screen.getByRole("option", { name: "Admin" }));

    expect(await within(dialog).findByText(/Admins can do everything/)).toBeInTheDocument();
  });

  it("shows username_taken inline and other refusals as a banner, keeping the form open", async () => {
    const user = userEvent.setup();
    const { onIssued, onOpenChange } = renderDialog();
    const dialog = await openDialog();

    await user.type(within(dialog).getByLabelText("Username"), "admin");
    await user.click(within(dialog).getByRole("button", { name: "Create invite link" }));
    expect(await within(dialog).findByText("That username is already taken.")).toBeInTheDocument();

    server.use(
      http.post("/api/dashboard-users", () =>
        HttpResponse.json({ error: { code: "admin_account_required", message: "x" } }, { status: 409 }),
      ),
    );
    await user.clear(within(dialog).getByLabelText("Username"));
    await user.type(within(dialog).getByLabelText("Username"), "newbie");
    await user.click(within(dialog).getByRole("button", { name: "Create invite link" }));
    expect(
      await within(dialog).findByText("Set a dashboard password and sign in before managing people."),
    ).toBeInTheDocument();
    expect(onIssued).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(within(dialog).getByLabelText("Username")).toHaveValue("newbie");
  });

  it("refuses a malformed or overlong username and an overlong display name before the round-trip", async () => {
    const user = userEvent.setup();
    renderDialog();
    const dialog = await openDialog();

    await user.type(within(dialog).getByLabelText("Username"), "bad name!");
    await user.click(within(dialog).getByRole("button", { name: "Create invite link" }));
    expect(await within(dialog).findByText("Use letters, digits, dots, dashes or underscores.")).toBeInTheDocument();

    await user.clear(within(dialog).getByLabelText("Username"));
    await user.type(within(dialog).getByLabelText("Username"), "a".repeat(65));
    await user.type(within(dialog).getByLabelText("Display name (optional)"), "b".repeat(129));
    await user.click(within(dialog).getByRole("button", { name: "Create invite link" }));
    expect(await within(dialog).findByText("Usernames can be at most 64 characters.")).toBeInTheDocument();
    expect(within(dialog).getByText("Display names can be at most 128 characters.")).toBeInTheDocument();
  });

  it("offers the password-less option only when a non-password provider is active and posts the expected identity", async () => {
    const user = userEvent.setup();
    const { onIssued, onOpenChange } = renderDialog();
    await openDialog();
    expect(screen.queryByTestId("invite-sso-option")).not.toBeInTheDocument();

    signInAsTeamAdmin({
      authMode: "trusted_header",
      loginHint: LoginHintSchema.parse({
        usernameField: "shown",
        providers: [
          { kind: "password", providerKey: "default", label: "Password", loginUrl: null },
          { kind: "trusted_header", providerKey: "default", label: "Authelia", loginUrl: null },
        ],
      }),
    });
    const dialog = await openDialog();
    expect(within(dialog).getByTestId("invite-sso-option")).toHaveTextContent("They sign in through Authelia.");

    let posted: unknown = null;
    server.use(
      http.post("/api/dashboard-users", async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json(
          {
            user: {
              id: "user_sarah",
              username: "sarah",
              displayName: null,
              email: null,
              role: { id: "role_operator", slug: "operator", name: "Operator", kind: "preset" },
              roleSource: "manual",
              status: "invited",
              isBreakGlass: false,
              totpConfigured: false,
              hasPassword: false,
              createdAt: new Date().toISOString(),
              lastLoginAt: null,
              pendingInvite: { expiresAt: null, ssoOnly: true },
            },
            invite: null,
          },
          { status: 201 },
        );
      }),
    );

    await user.type(within(dialog).getByLabelText("Username"), "sarah");
    await user.click(within(dialog).getByLabelText("Add without a password"));
    await user.click(within(dialog).getByRole("button", { name: "Add person" }));
    expect(await within(dialog).findByText("Enter the identity the proxy will send.")).toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Identity the proxy sends"), " Sarah@Example.com ");
    await user.click(within(dialog).getByRole("button", { name: "Add person" }));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(posted).toMatchObject({
      username: "sarah",
      ssoOnly: true,
      expectedIdentity: { provider: "trusted_header", providerKey: "default", subject: "Sarah@Example.com" },
    });
    // No link to hand over.
    expect(onIssued).not.toHaveBeenCalled();
  });
});
