import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  changePassword,
  removePassword,
  setupPassword,
} from "@/features/auth/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { PasswordSettings } from "@/features/settings/components/password-settings";

vi.mock("@/features/auth/api", () => ({
  setupPassword: vi.fn(),
  changePassword: vi.fn(),
  removePassword: vi.fn(),
}));

describe("PasswordSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({
      passwordRequired: false,
      refreshSession: vi.fn().mockResolvedValue(undefined),
    });
  });

  it("shows setup button when no password is set", () => {
    render(<PasswordSettings />);
    expect(screen.getByRole("button", { name: "Set password" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change" })).not.toBeInTheDocument();
  });

  it("shows change/remove buttons when password is configured", () => {
    useAuthStore.setState({ passwordRequired: true });
    render(<PasswordSettings />);
    expect(screen.getByRole("button", { name: "Change" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set password" })).not.toBeInTheDocument();
  });

  it("handles setup flow via dialog", async () => {
    const user = userEvent.setup();
    vi.mocked(setupPassword).mockResolvedValue({} as never);

    render(<PasswordSettings />);

    await user.click(screen.getByRole("button", { name: "Set password" }));
    // Dialog opens
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Password"), "new-password-1");
    await user.click(screen.getAllByRole("button", { name: "Set password" }).find((btn) => btn.getAttribute("type") === "submit")!);
    expect(setupPassword).toHaveBeenCalledWith({ password: "new-password-1" });
  });

  it("handles change flow via dialog", async () => {
    const user = userEvent.setup();
    useAuthStore.setState({ passwordRequired: true });
    vi.mocked(changePassword).mockResolvedValue({} as never);

    render(<PasswordSettings />);

    await user.click(screen.getByRole("button", { name: "Change" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Current password"), "current-password");
    await user.type(screen.getByLabelText("New password"), "changed-password");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(changePassword).toHaveBeenCalledWith({
      currentPassword: "current-password",
      newPassword: "changed-password",
    });
  });

  it("handles remove flow via dialog", async () => {
    const user = userEvent.setup();
    useAuthStore.setState({ passwordRequired: true });
    vi.mocked(removePassword).mockResolvedValue({} as never);

    render(<PasswordSettings />);

    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Current password"), "changed-password");
    await user.click(screen.getByRole("button", { name: "Remove password" }));
    expect(removePassword).toHaveBeenCalledWith({ password: "changed-password" });
  });

  it("shows error message on request failure", async () => {
    const user = userEvent.setup();
    vi.mocked(setupPassword).mockRejectedValue(new Error("setup failed"));

    render(<PasswordSettings />);

    await user.click(screen.getByRole("button", { name: "Set password" }));
    await user.type(screen.getByLabelText("Password"), "new-password-1");
    await user.click(screen.getAllByRole("button", { name: "Set password" }).find((btn) => btn.getAttribute("type") === "submit")!);

    expect(await screen.findByText("setup failed")).toBeInTheDocument();
  });
});
