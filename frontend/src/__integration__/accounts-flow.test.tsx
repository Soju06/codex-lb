import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { renderWithProviders } from "@/test/utils";

describe("accounts flow integration", () => {
  it("supports account selection and pause/resume actions", async () => {
    const user = userEvent.setup({ delay: null });

    window.history.pushState({}, "", "/accounts");
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Accounts" })).toBeInTheDocument();
    expect((await screen.findAllByText("primary@example.com")).length).toBeGreaterThan(0);
    expect(screen.getByText("secondary@example.com")).toBeInTheDocument();

    await user.click(screen.getByText("secondary@example.com"));
    expect(await screen.findByText("Token Status")).toBeInTheDocument();

    const resumeButton = screen.queryByRole("button", { name: "Resume" });
    if (resumeButton) {
      await user.click(resumeButton);
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
      });
    } else {
      await user.click(screen.getByRole("button", { name: "Pause" }));
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
      });
    }
  });

  it("lets operators set, search, and clear an account alias", async () => {
    const user = userEvent.setup({ delay: null });

    window.history.pushState({}, "", "/accounts");
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Accounts" })).toBeInTheDocument();
    const aliasInput = await screen.findByLabelText("Account alias");
    await user.clear(aliasInput);
    await user.type(aliasInput, "Personal Plus");
    await user.click(screen.getByRole("button", { name: "Save alias" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Personal Plus" })).toBeInTheDocument();
    });

    await user.type(screen.getByPlaceholderText("Search accounts..."), "personal");
    expect(screen.getAllByText("Personal Plus").length).toBeGreaterThan(0);
    expect(screen.queryByText("secondary@example.com")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear alias" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "primary@example.com" })).toBeInTheDocument();
    });
  });
});
