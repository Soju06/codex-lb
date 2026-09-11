import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { AccessCard } from "@/features/settings/components/access/access-card";
import { renderAt, settings, signInAsTeamAdmin } from "@/test/access-test-utils";
import { OPERATOR_PERMISSIONS, VIEWER_PERMISSIONS, createAccessSummary, createSessionUser } from "@/test/mocks/factories";
import { MOCK_ISSUED_INVITE_TOKEN } from "@/test/mocks/handlers";

vi.mock("@/features/settings/components/guest-access-settings", () => ({
  GuestAccessSettings: () => <div data-testid="control">Guest Access Settings</div>,
}));
vi.mock("@/features/settings/components/password-settings", () => ({
  PasswordSettings: () => <div data-testid="control">Password Settings</div>,
}));
vi.mock("@/features/settings/components/session-settings", () => ({
  SessionSettings: () => <div data-testid="control">Session Settings</div>,
}));
vi.mock("@/features/settings/components/totp-settings", () => ({
  TotpSettings: () => (
    <section id="totp" data-testid="control">
      TOTP Settings
    </section>
  ),
}));

const TODAYS_ORDER = ["Guest Access Settings", "Password Settings", "Session Settings", "TOTP Settings"];
// Guest access and session length are security settings; without `security:write` only the personal controls render.
const PERSONAL_ORDER = ["Password Settings", "TOTP Settings"];

function renderCard(initialEntry = "/settings") {
  return renderAt(
    <>
      <AccessCard settings={settings} busy={false} onSave={vi.fn().mockResolvedValue(undefined)} onRefresh={vi.fn()} />
      <Link to="/settings#access-people">go to people</Link>
    </>,
    initialEntry,
  );
}

async function controlLabels() {
  await screen.findByText("TOTP Settings");
  return screen.getAllByTestId("control").map((node) => node.textContent);
}

function tabState(name: string) {
  return screen.getByRole("tab", { name }).getAttribute("aria-selected");
}

describe("AccessCard", () => {
  beforeEach(() => {
    signInAsTeamAdmin({ accessSummary: createAccessSummary(), tier: "individual" });
  });

  it("individual tier: one line, the invite button and today's four controls in today's order", async () => {
    renderCard();

    expect(screen.getByRole("heading", { name: "Access" })).toBeInTheDocument();
    expect(screen.getByText(/Only you are using this dashboard/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Invite a teammate" })).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(await controlLabels()).toEqual(TODAYS_ORDER);
    // Individual-tier copy never says user, role, SSO, SCIM, IdP or RBAC.
    expect(screen.getByTestId("access-solo-line").textContent).not.toMatch(/\b(user|role|SSO|SCIM|IdP|RBAC)\b/i);
  });

  it("individual tier without a password-backed account asks for a password first", () => {
    useAuthStore.setState({ user: null, passwordRequired: false, passwordSessionActive: false });

    renderCard();

    expect(screen.getByText("To invite teammates, set a password first.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set password" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Invite a teammate" })).not.toBeInTheDocument();
  });

  it("renders nothing about people for a principal without users:manage on the individual tier", async () => {
    useAuthStore.setState({ permissions: OPERATOR_PERMISSIONS, accessSummary: null, user: null });

    renderCard();

    expect(screen.queryByTestId("access-solo-line")).not.toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(await controlLabels()).toEqual(PERSONAL_ORDER);
  });

  it("keeps the first invite link on screen through the tier flip, then switches to People", async () => {
    const user = userEvent.setup();
    // A real refresh: the second row exists now, so the derived tier becomes team.
    const refreshSession = vi.fn(async () => {
      useAuthStore.setState({
        accessSummary: createAccessSummary({ usersTotal: 2, usersInvited: 1, pendingInvites: 1 }),
        tier: "team",
      });
      return useAuthStore.getState() as never;
    });
    useAuthStore.setState({ refreshSession });
    renderCard();

    await user.click(screen.getByRole("button", { name: "Invite a teammate" }));
    const dialog = await screen.findByRole("dialog", { name: "Invite a teammate" });
    await waitFor(() => expect(within(dialog).getByRole("combobox", { name: "Role" })).toHaveTextContent("Operator"));
    await user.type(within(dialog).getByLabelText("Username"), "sarah");
    await user.click(within(dialog).getByRole("button", { name: "Create invite link" }));

    const linkDialog = await screen.findByRole("dialog", { name: "Invite link" });
    expect(within(linkDialog).getByTestId("invite-link")).toHaveTextContent(`/invite/${MOCK_ISSUED_INVITE_TOKEN}`);
    expect(linkDialog).toHaveTextContent("Hand this link to sarah.");
    expect(refreshSession).not.toHaveBeenCalled();
    expect(useAuthStore.getState().tier).toBe("individual");

    await user.click(within(linkDialog).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(refreshSession).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("tablist")).toBeInTheDocument();
    expect(tabState("People")).toBe("true");
    expect(await screen.findByTestId("people-row-sarah")).toBeInTheDocument();

    // The link was shown once: reopening the invite dialog starts a fresh form.
    await user.click(screen.getByRole("button", { name: "Invite" }));
    const again = await screen.findByRole("dialog", { name: "Invite a teammate" });
    expect(within(again).getByLabelText("Username")).toHaveValue("");
    expect(screen.queryByTestId("invite-link")).not.toBeInTheDocument();
    expect(screen.queryByText(MOCK_ISSUED_INVITE_TOKEN, { exact: false })).not.toBeInTheDocument();
  });

  it("team tier: People and My sign-in tabs, People first", async () => {
    const user = userEvent.setup();
    signInAsTeamAdmin();

    renderCard();

    expect(screen.getByRole("tablist", { name: "Access" })).toBeInTheDocument();
    expect(tabState("People")).toBe("true");
    expect(await screen.findByTestId("people-row-ops")).toBeInTheDocument();
    expect(screen.queryByTestId("control")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "My sign-in" }));
    expect(await controlLabels()).toEqual(TODAYS_ORDER);
    expect(screen.queryByTestId("people-row-ops")).not.toBeInTheDocument();
  });

  it.each([
    ["without users:manage", { permissions: OPERATOR_PERMISSIONS, accessSummary: null, assignableRoleIds: [], user: createSessionUser({ id: "user_ops", username: "ops" }) }, PERSONAL_ORDER],
    ["as a viewer without write", { permissions: VIEWER_PERMISSIONS, canWrite: false, accessSummary: null, assignableRoleIds: [], user: createSessionUser({ id: "user_viewer", username: "viewer" }) }, PERSONAL_ORDER],
    ["standard mode without an account", { user: null }, TODAYS_ORDER],
    ["on a trusted-header install without an account", { user: null, authMode: "trusted_header" as const }, TODAYS_ORDER],
    ["when auth is disabled", { user: null, authMode: "disabled" as const }, TODAYS_ORDER],
  ])("team tier %s shows only the sign-in controls, no tabs, no invite", async (_label, overrides, order) => {
    signInAsTeamAdmin({ accessSummary: createAccessSummary({ usersTotal: 2, nonAdminUsers: 1 }), ...overrides });

    renderCard();

    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Invite/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("access-solo-line")).not.toBeInTheDocument();
    expect(await controlLabels()).toEqual(order);
  });

  it("offers People to a reverse-proxy account that manages users", async () => {
    signInAsTeamAdmin({ authMode: "trusted_header", accessSummary: createAccessSummary({ usersTotal: 2, nonAdminUsers: 1 }) });

    renderCard();

    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(await screen.findByTestId("people-row-ops")).toBeInTheDocument();
  });

  it("#access-people selects People, #access selects My sign-in, #totp reaches the TOTP section", async () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
    signInAsTeamAdmin();

    let view = renderCard("/settings#access-people");
    expect(tabState("People")).toBe("true");
    await vi.waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
    expect((scrollIntoView.mock.contexts[0] as HTMLElement).id).toBe("access");
    view.unmount();

    view = renderCard("/settings#access");
    expect(tabState("My sign-in")).toBe("true");
    expect(await controlLabels()).toEqual(TODAYS_ORDER);
    view.unmount();

    scrollIntoView.mockClear();
    renderCard("/settings#totp");
    expect(tabState("My sign-in")).toBe("true");
    await controlLabels();
    await vi.waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
    expect((scrollIntoView.mock.contexts[0] as HTMLElement).id).toBe("totp");
    scrollIntoView.mockRestore();
  });

  it("a clicked tab holds only until the next navigation, even to the same hash", async () => {
    const user = userEvent.setup();
    signInAsTeamAdmin();
    renderCard("/settings#access-people");

    await user.click(screen.getByRole("tab", { name: "My sign-in" }));
    expect(tabState("My sign-in")).toBe("true");

    await user.click(screen.getByRole("link", { name: "go to people" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/settings#access-people");
    await waitFor(() => expect(tabState("People")).toBe("true"));
  });
});
