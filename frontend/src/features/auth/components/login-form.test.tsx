import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/features/auth/components/login-form";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { LAST_USERNAME_STORAGE_KEY } from "@/features/auth/last-username";
import { LoginHintSchema } from "@/features/auth/schemas";
import { ApiError } from "@/lib/api-client";

describe("LoginForm", () => {
  beforeEach(() => {
    window.localStorage.removeItem(LAST_USERNAME_STORAGE_KEY);
    useAuthStore.setState({
      clearError: useAuthStore.getInitialState().clearError,
      loading: false,
      error: null,
      passwordRequired: true,
      guestAccessEnabled: false,
      guestPasswordRequired: false,
      loginHint: LoginHintSchema.parse({ usernameField: "hidden" }),
      loginGuest: vi.fn(),
    });
  });

  it("renders and submits password", async () => {
    const user = userEvent.setup();
    const clearError = vi.fn();
    const login = vi.fn().mockResolvedValue(undefined);

    useAuthStore.setState({
      clearError,
      login,
      loading: false,
      error: null,
    });

    render(<LoginForm />);

    await user.type(screen.getByLabelText("Password"), "secret-pass");
    await user.click(screen.getByRole("button", { name: "Sign In" }));

    expect(clearError).toHaveBeenCalledTimes(1);
    expect(login).toHaveBeenCalledWith("secret-pass");
  });

  it("shows error message when present", () => {
    useAuthStore.setState({
      error: "Invalid credentials",
      loading: false,
    });

    render(<LoginForm />);
    expect(screen.getByText("Invalid credentials")).toBeInTheDocument();
  });

  it("renders and submits guest password when guest access is enabled", async () => {
    const user = userEvent.setup();
    const clearError = vi.fn();
    const loginGuest = vi.fn().mockResolvedValue(undefined);

    useAuthStore.setState({
      clearError,
      loginGuest,
      passwordRequired: false,
      guestAccessEnabled: true,
      guestPasswordRequired: true,
      loading: false,
      error: null,
    });

    render(<LoginForm />);

    await user.type(screen.getByLabelText("Guest password"), "guest-pass");
    await user.click(screen.getByRole("button", { name: "View as Guest" }));

    expect(clearError).toHaveBeenCalledTimes(1);
    expect(loginGuest).toHaveBeenCalledWith("guest-pass");
  });

  it("submits passwordless guest access without a password", async () => {
    const user = userEvent.setup();
    const clearError = vi.fn();
    const loginGuest = vi.fn().mockResolvedValue(undefined);

    useAuthStore.setState({
      clearError,
      loginGuest,
      passwordRequired: false,
      guestAccessEnabled: true,
      guestPasswordRequired: false,
      loading: false,
      error: null,
    });

    render(<LoginForm />);

    await user.click(screen.getByRole("button", { name: "View as Guest" }));

    expect(clearError).toHaveBeenCalledTimes(1);
    expect(loginGuest).toHaveBeenCalledWith(undefined);
  });

  it("disables input and submit while loading", () => {
    useAuthStore.setState({
      loading: true,
      error: null,
    });

    render(<LoginForm />);
    expect(screen.getByLabelText("Password")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Sign In" })).toBeDisabled();
  });

  describe("username disclosure", () => {
    it("hides the username field on a single-account install and reveals it through the link", async () => {
      const user = userEvent.setup();
      const login = vi.fn().mockResolvedValue(undefined);
      useAuthStore.setState({ login, clearError: vi.fn() });

      render(<LoginForm />);

      expect(screen.queryByLabelText("Username")).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Sign in with a different account" }));

      await user.type(screen.getByLabelText("Username"), "alice");
      await user.type(screen.getByLabelText("Password"), "secret-pass");
      await user.click(screen.getByRole("button", { name: "Sign In" }));

      expect(login).toHaveBeenCalledWith("secret-pass", "alice");
      expect(screen.queryByRole("button", { name: "Sign in with a different account" })).not.toBeInTheDocument();
    });

    it("shows the username field when the server says so and prefills the remembered username", () => {
      window.localStorage.setItem(LAST_USERNAME_STORAGE_KEY, "alice");
      useAuthStore.setState({ loginHint: LoginHintSchema.parse({ usernameField: "shown" }) });

      render(<LoginForm />);

      expect(screen.getByLabelText("Username")).toHaveValue("alice");
      expect(screen.queryByRole("button", { name: "Sign in with a different account" })).not.toBeInTheDocument();
    });

    it("leaves the username empty on a multi-account install with nothing remembered", () => {
      useAuthStore.setState({ loginHint: LoginHintSchema.parse({ usernameField: "shown" }) });

      render(<LoginForm />);

      expect(screen.getByLabelText("Username")).toHaveValue("");
    });

    it("keeps the username field visible while a non-default username is remembered even when the server hides it", () => {
      window.localStorage.setItem(LAST_USERNAME_STORAGE_KEY, "alice");

      render(<LoginForm />);

      expect(screen.getByLabelText("Username")).toHaveValue("alice");
      expect(screen.getByText("Enter your username and password to continue.")).toBeInTheDocument();
    });

    // The account the install bootstrapped can be renamed, so no particular
    // name may mean "this is the default": a remembered name reveals the field
    // whatever it says, and the store forgets it after a sign-in that comes
    // back `hidden` (see use-auth) so the install converges on its own.
    it("reveals the field for any remembered name, the bootstrap default included", () => {
      window.localStorage.setItem(LAST_USERNAME_STORAGE_KEY, "admin");

      render(<LoginForm />);

      expect(screen.getByLabelText("Username")).toHaveValue("admin");
      expect(screen.getByText("Enter your username and password to continue.")).toBeInTheDocument();
    });

    it("is password-only again once nothing is remembered", () => {
      render(<LoginForm />);

      expect(screen.queryByLabelText("Username")).not.toBeInTheDocument();
      expect(screen.getByText("Enter your admin password to continue.")).toBeInTheDocument();
    });

    it("focuses the username field once the link reveals it", async () => {
      const user = userEvent.setup();

      render(<LoginForm />);
      await user.click(screen.getByRole("button", { name: "Sign in with a different account" }));

      await waitFor(() => expect(screen.getByLabelText("Username")).toHaveFocus());
    });

    it("reveals and focuses the username field with an inline message (and no banner) on username_required", async () => {
      const user = userEvent.setup();
      const login = vi
        .fn()
        .mockImplementationOnce(async () => {
          // What the real store does on a failed request: keep the server message as the banner.
          useAuthStore.setState({ error: "Username is required" });
          throw new ApiError({ status: 422, code: "username_required", message: "Username is required" });
        })
        .mockResolvedValue(undefined);
      useAuthStore.setState({ login });

      render(<LoginForm />);

      await user.type(screen.getByLabelText("Password"), "secret-pass");
      await user.click(screen.getByRole("button", { name: "Sign In" }));

      expect(login).toHaveBeenCalledWith("secret-pass");
      expect(await screen.findByText("Enter the username to sign in with.")).toBeInTheDocument();
      expect(screen.queryByText("Username is required")).not.toBeInTheDocument();
      expect(useAuthStore.getState().error).toBeNull();
      await waitFor(() => expect(screen.getByLabelText("Username")).toHaveFocus());

      await user.type(screen.getByLabelText("Username"), "alice");
      await user.click(screen.getByRole("button", { name: "Sign In" }));

      expect(login).toHaveBeenLastCalledWith("secret-pass", "alice");
    });

    it("omits the username when the field is shown but left blank", async () => {
      const user = userEvent.setup();
      const login = vi.fn().mockResolvedValue(undefined);
      useAuthStore.setState({ login, clearError: vi.fn(), loginHint: LoginHintSchema.parse({ usernameField: "shown" }) });

      render(<LoginForm />);

      await user.type(screen.getByLabelText("Password"), "secret-pass");
      await user.click(screen.getByRole("button", { name: "Sign In" }));

      expect(login).toHaveBeenCalledWith("secret-pass");
    });
  });
  describe("login policy", () => {
    const PROXY = { kind: "trusted_header", providerKey: "default", label: "Reverse proxy", loginUrl: null };
    const SSO = { kind: "oidc", providerKey: "okta", label: "Okta", loginUrl: "https://sso.example.com/start" };

    it("renders the form as usual when nothing is restricted", () => {
      render(<LoginForm localForm="shown" />);

      expect(screen.getByLabelText("Password")).toBeInTheDocument();
      expect(screen.queryByText("Sign in with a password instead")).not.toBeInTheDocument();
    });

    it("collapses the form behind a link under admins_only and reveals it in place", async () => {
      const user = userEvent.setup();
      render(<LoginForm localForm="collapsed" />);

      expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Sign in with a password instead" }));

      expect(screen.getByLabelText("Password")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Sign in with a password instead" })).not.toBeInTheDocument();
    });

    it("renders no field and no reveal link under break_glass_only, only the providers", () => {
      useAuthStore.setState({
        loginHint: LoginHintSchema.parse({ usernameField: "hidden", providers: [PROXY], localLogin: "break_glass_only" }),
      });
      render(<LoginForm localForm="hidden" />);

      expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Sign in with a password instead" })).not.toBeInTheDocument();
      expect(screen.getByTestId("login-providers")).toHaveTextContent("Reverse proxy");
    });

    it("says the door is shut rather than rendering an empty card", () => {
      useAuthStore.setState({
        loginHint: LoginHintSchema.parse({ usernameField: "hidden", localLogin: "break_glass_only" }),
      });
      render(<LoginForm localForm="hidden" />);

      expect(screen.getByText("Signing in with a password is restricted on this install.")).toBeInTheDocument();
    });

    it("puts the company sign-in first and never names an account", () => {
      window.localStorage.setItem(LAST_USERNAME_STORAGE_KEY, "rescue");
      useAuthStore.setState({
        loginHint: LoginHintSchema.parse({
          usernameField: "shown",
          providers: [
            { kind: "password", providerKey: "default", label: "Password", loginUrl: null },
            SSO,
          ],
          localLogin: "admins_only",
        }),
      });
      const { container } = render(<LoginForm localForm="collapsed" />);

      const providers = screen.getByTestId("login-providers");
      const link = screen.getByRole("link", { name: "Continue with Okta" });
      expect(link).toHaveAttribute("href", "https://sso.example.com/start");
      // The provider block is drawn before the local password disclosure.
      expect(providers.compareDocumentPosition(screen.getByRole("button", { name: "Sign in with a password instead" })))
        .toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      // The local password provider is not offered twice.
      expect(providers).not.toHaveTextContent("Password");
      // The only name on screen is the one this browser remembered.
      expect(container.textContent).not.toContain("admin");
    });

    it("tells the person where the reverse proxy sends them instead of drawing a dead button", () => {
      useAuthStore.setState({
        loginHint: LoginHintSchema.parse({ usernameField: "shown", providers: [PROXY], localLogin: "admins_only" }),
      });
      render(<LoginForm localForm="collapsed" />);

      expect(screen.getByTestId("login-providers")).toHaveTextContent(
        "Sign in through Reverse proxy first, then open this dashboard again.",
      );
      expect(screen.queryByRole("link", { name: /Reverse proxy/ })).not.toBeInTheDocument();
    });

    it("keeps the guest block reachable while the password form is hidden", () => {
      useAuthStore.setState({
        guestAccessEnabled: true,
        guestPasswordRequired: false,
        loginHint: LoginHintSchema.parse({ usernameField: "hidden", localLogin: "break_glass_only" }),
      });
      render(<LoginForm localForm="hidden" />);

      expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "View as Guest" })).toBeInTheDocument();
    });
  });
});
