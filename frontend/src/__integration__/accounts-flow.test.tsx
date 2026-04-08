import { screen, waitFor, within } from "@testing-library/react";
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

  it("supports editing a platform fallback identity", async () => {
    const user = userEvent.setup({ delay: null });

    window.history.pushState({}, "", "/accounts");
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Accounts" })).toBeInTheDocument();
    expect((await screen.findAllByText("primary@example.com")).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: /Add API/i }));
    await user.type(screen.getByLabelText("Label"), "Platform Initial");
    await user.type(screen.getByLabelText("API key"), "sk-platform-test");
    await user.click(screen.getByRole("button", { name: "Add API key" }));

    await waitFor(() => {
      expect(screen.getAllByText("Platform Initial").length).toBeGreaterThan(0);
    });

    await user.click(screen.getAllByRole("button", { name: /Platform Initial/ })[0]);
    await user.click(await screen.findByRole("button", { name: /Edit/i }));

    const dialog = await screen.findByRole("dialog", { name: "Edit OpenAI Platform API key" });
    await user.clear(within(dialog).getByLabelText("Label"));
    await user.type(within(dialog).getByLabelText("Label"), "Platform Renamed");
    await user.click(within(dialog).getAllByRole("checkbox")[0]);
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(screen.getAllByText("Platform Renamed").length).toBeGreaterThan(0);
    });
    expect(screen.getByText(/Fallback HTTP \/v1\/models/)).toBeInTheDocument();
  });
});
