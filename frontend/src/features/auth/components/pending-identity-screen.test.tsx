import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PendingIdentityScreen } from "@/features/auth/components/pending-identity-screen";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { LoginHintSchema } from "@/features/auth/schemas";
import { maskEmail } from "@/features/organisation/rules";

const PASSWORD = { kind: "password", providerKey: "default", label: "Password", loginUrl: null };
const SSO = { kind: "oidc", providerKey: "default", label: "Okta", loginUrl: "/api/dashboard-auth/oidc/login/start" };
// The address the identity provider asserted, as the administrator's refused
// sign-ins list shows it. The person never sees this; they see its masked form.
const REFUSED_ADDRESS = ["stranger", "example.com"].join("@");

function signedOut(hint: Record<string, unknown> = {}) {
  useAuthStore.setState({
    initialized: true,
    authenticated: false,
    localPasswordConfigured: false,
    refreshSession: vi.fn().mockResolvedValue(undefined),
    logout: vi.fn().mockResolvedValue(undefined),
    loginHint: LoginHintSchema.parse({ usernameField: "shown", providers: [], ...hint }),
  });
}

function renderScreen(localForm: "shown" | "collapsed" | "hidden" = "shown") {
  return render(
    <MemoryRouter>
      <PendingIdentityScreen localForm={localForm} />
    </MemoryRouter>,
  );
}

describe("PendingIdentityScreen", () => {
  beforeEach(() => {
    signedOut();
  });

  it("names the provider and shows the server's reference as the thing to quote", () => {
    signedOut({
      pendingIdentity: true,
      pendingArrival: { provider: "Okta", reference: maskEmail(REFUSED_ADDRESS) },
    });

    const { container } = renderScreen();

    expect(screen.getByText("Your account is not ready yet")).toBeInTheDocument();
    expect(screen.getByText("Signed in through Okta")).toBeInTheDocument();
    expect(screen.getByTestId("pending-identity")).toHaveTextContent(
      "Okta recognised you, but this dashboard has no account for you yet.",
    );
    expect(screen.getByTestId("pending-reference")).toHaveTextContent("s***@example.com");
    expect(screen.getByText(/Give your administrator this reference/)).toBeInTheDocument();
    // The address itself never arrives here, and neither does anything about
    // the connection or about who already has an account.
    expect(container.textContent).not.toContain(REFUSED_ADDRESS);
    expect(container.textContent).not.toMatch(/issuer|client id|claim|subject|group|admin@/i);
  });

  it("renders the reference verbatim and computes nothing", () => {
    // A masking rule this screen disagreed with would hand out a reference no
    // administrator could find, so it renders whatever the server said.
    signedOut({ pendingIdentity: true, pendingArrival: { provider: "Okta", reference: "***@example.com" } });

    renderScreen();

    expect(screen.getByTestId("pending-reference")).toHaveTextContent("***@example.com");
  });

  it("keeps its general copy when no arrival was carried, and guesses no provider", () => {
    // One company sign-in is active, which is exactly the temptation: the
    // screen still must not claim that is the one that refused somebody.
    signedOut({ pendingIdentity: true, providers: [PASSWORD, SSO] });

    const { container } = renderScreen();

    expect(screen.getByText("Signed in through your company proxy")).toBeInTheDocument();
    expect(screen.getByTestId("pending-identity")).toHaveTextContent("Ask an administrator to add you");
    expect(screen.queryByTestId("pending-reference")).not.toBeInTheDocument();
    expect(container.textContent).not.toContain("Okta");
  });

  it("offers the local form beneath it when one exists and the policy allows it", () => {
    useAuthStore.setState({ localPasswordConfigured: true });
    signedOut({ pendingIdentity: true, providers: [PASSWORD, SSO] });
    useAuthStore.setState({ localPasswordConfigured: true });

    renderScreen("shown");

    expect(screen.getByTestId("pending-local-login")).toBeInTheDocument();
  });

  it("is not a second door under break_glass_only", () => {
    signedOut({
      pendingIdentity: true,
      providers: [PASSWORD, SSO],
      localLogin: "break_glass_only",
      pendingArrival: { provider: "Okta", reference: maskEmail(REFUSED_ADDRESS) },
    });
    useAuthStore.setState({ localPasswordConfigured: true });

    renderScreen("hidden");

    expect(screen.queryByTestId("pending-local-login")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Emergency sign-in with a password" })).toHaveAttribute(
      "href",
      "/login?local=1",
    );
  });

  it("retries and signs out through the store", async () => {
    const refreshSession = vi.fn().mockResolvedValue(undefined);
    const logout = vi.fn().mockResolvedValue(undefined);
    signedOut({ pendingIdentity: true });
    useAuthStore.setState({ refreshSession, logout });

    renderScreen();
    screen.getByRole("button", { name: "Try again" }).click();
    screen.getByRole("button", { name: "Logout" }).click();

    expect(refreshSession).toHaveBeenCalledTimes(1);
    expect(logout).toHaveBeenCalledTimes(1);
  });
});
