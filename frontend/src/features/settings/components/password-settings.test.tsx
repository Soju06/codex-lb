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
      refreshSession: vi.fn().mockResolvedValue(undefined),
    });
  });

  it("handles setup/change/remove flows", async () => {
    const user = userEvent.setup();
    vi.mocked(setupPassword).mockResolvedValue({} as never);
    vi.mocked(changePassword).mockResolvedValue({} as never);
    vi.mocked(removePassword).mockResolvedValue({} as never);

    render(<PasswordSettings />);

    await user.type(screen.getByLabelText("Setup password"), "new-password-1");
    await user.click(screen.getByRole("button", { name: "Setup" }));
    expect(setupPassword).toHaveBeenCalledWith({ password: "new-password-1" });
    expect(await screen.findByText("Password configured.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Current password"), "current-password");
    await user.type(screen.getByLabelText("New password"), "changed-password");
    await user.click(screen.getByRole("button", { name: "Change" }));
    expect(changePassword).toHaveBeenCalledWith({
      currentPassword: "current-password",
      newPassword: "changed-password",
    });
    expect(await screen.findByText("Password changed.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Confirm password to remove"), "changed-password");
    await user.click(screen.getByRole("button", { name: "Remove password" }));
    expect(removePassword).toHaveBeenCalledWith({ password: "changed-password" });
    expect(await screen.findByText("Password removed.")).toBeInTheDocument();
  });

  it("shows error message on request failure", async () => {
    const user = userEvent.setup();
    vi.mocked(setupPassword).mockRejectedValue(new Error("setup failed"));

    render(<PasswordSettings />);
    await user.type(screen.getByLabelText("Setup password"), "new-password-1");
    await user.click(screen.getByRole("button", { name: "Setup" }));

    expect(await screen.findByText("setup failed")).toBeInTheDocument();
  });
});
