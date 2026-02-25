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
    });
  });

  it("renders and submits username/password", async () => {
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

    await user.clear(screen.getByLabelText("Username"));
    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "secret-pass");
    await user.click(screen.getByRole("button", { name: "Sign In" }));

    expect(clearError).toHaveBeenCalledTimes(1);
    expect(login).toHaveBeenCalledWith("admin", "secret-pass");
  });

  it("shows error message when present", () => {
    useAuthStore.setState({
      error: "Invalid credentials",
      loading: false,
    });

    render(<LoginForm />);
    expect(screen.getByText("Invalid credentials")).toBeInTheDocument();
  });

  it("disables input and submit while loading", () => {
    useAuthStore.setState({
      loading: true,
      error: null,
    });

    render(<LoginForm />);
    expect(screen.getByLabelText("Username")).toBeDisabled();
    expect(screen.getByLabelText("Password")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Sign In" })).toBeDisabled();
  });
});
