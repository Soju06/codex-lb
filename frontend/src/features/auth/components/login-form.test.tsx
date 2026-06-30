import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/features/auth/components/login-form";
import { useAuthStore } from "@/features/auth/hooks/use-auth";

describe("LoginForm", () => {
  beforeEach(() => {
    useAuthStore.setState({
      loading: false,
      error: null,
      passwordRequired: true,
      guestAccessEnabled: false,
      guestPasswordRequired: false,
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
});
