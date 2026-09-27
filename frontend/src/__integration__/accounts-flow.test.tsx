import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { renderWithProviders } from "@/test/utils";

describe("accounts flow integration", () => {
  it("preserves search across views, manages a grid account, and remembers grid view", async () => {
    const user = userEvent.setup({ delay: null });
    window.history.pushState({}, "", "/accounts");
    const view = renderWithProviders(<App />);
    const search = await screen.findByPlaceholderText("Search accounts...");
    await user.type(search, "secondary");
    await user.click(screen.getByRole("button", { name: "Grid view" }));
    expect(search).toHaveValue("secondary");
    expect(screen.getAllByTestId("account-grid-card")).toHaveLength(1);
    expect(window.localStorage.getItem("codex-lb-accounts-view-mode")).toBe("grid");
    await user.click(screen.getByRole("button", { name: "View details" }));
    const detail = within(await screen.findByRole("dialog"));
    expect(detail.getByRole("heading", { name: "secondary@example.com" })).toBeInTheDocument();
    await user.click(detail.getByRole("button", { name: "Resume" }));
    expect(await detail.findByRole("button", { name: "Pause" })).toBeInTheDocument();
    await user.click(detail.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("Search accounts...")).toHaveValue("secondary");
    await user.click(screen.getByRole("button", { name: "List view" }));
    expect(screen.getByPlaceholderText("Search accounts...")).toHaveValue("secondary");
    await user.click(screen.getByRole("button", { name: "Detail view" }));
    expect(screen.getByPlaceholderText("Search accounts...")).toHaveValue("secondary");
    expect(within(screen.getByTestId("accounts-inline-detail")).getByRole("heading", { name: "secondary@example.com" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Grid view" }));
    view.unmount();
    renderWithProviders(<App />);
    expect(await screen.findByRole("button", { name: "Grid view" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findAllByTestId("account-grid-card")).toHaveLength(2);
    window.localStorage.removeItem("codex-lb-accounts-view-mode");
  });

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
    await user.click(await screen.findByRole("button", { name: /primary@example.com/ }));
    await user.click(await screen.findByRole("button", { name: "Edit alias" }));
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
    await user.click(screen.getByRole("button", { name: /Personal Plus/ }));

    await user.click(await screen.findByRole("button", { name: "Edit alias" }));
    const aliasInputToClear = await screen.findByLabelText("Account alias");
    await user.clear(aliasInputToClear);
    await user.click(screen.getByRole("button", { name: "Save alias" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "primary@example.com" })).toBeInTheDocument();
    });
  });
});
