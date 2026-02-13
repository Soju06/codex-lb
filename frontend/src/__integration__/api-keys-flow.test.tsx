import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { renderWithProviders } from "@/test/utils";

describe("api keys flow integration", () => {
  it("creates, shows plain key dialog, edits, and deletes an api key", async () => {
    const user = userEvent.setup();
    const createdName = "Integration Key";
    const updatedName = "Integration Key Updated";

    window.history.pushState({}, "", "/settings");
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(await screen.findByText("API Keys")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create key" }));
    await user.type(screen.getByLabelText("Name"), createdName);
    await user.click(screen.getByRole("button", { name: "Create" }));

    const createdTitle = await screen.findByText("API key created");
    expect(screen.getByText(/sk-test-generated/i)).toBeInTheDocument();
    const createdDialog = createdTitle.closest("[role='dialog']");
    expect(createdDialog).not.toBeNull();
    const closeCandidates = within(createdDialog as HTMLElement).getAllByRole("button", {
      name: "Close",
    });
    const closeButton =
      closeCandidates.find((element) => element.getAttribute("data-slot") === "button") ??
      closeCandidates[0];
    await user.click(closeButton);

    const createdRow = await screen.findByText(createdName);
    const createdTr = createdRow.closest("tr");
    expect(createdTr).not.toBeNull();

    await user.click(within(createdTr as HTMLElement).getByRole("button", { name: "Edit" }));
    const nameInput = await screen.findByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, updatedName);
    await user.click(screen.getByRole("button", { name: "Save" }));

    const updatedRow = await screen.findByText(updatedName);
    const updatedTr = updatedRow.closest("tr");
    expect(updatedTr).not.toBeNull();

    await user.click(within(updatedTr as HTMLElement).getByRole("button", { name: "Delete" }));
    const confirmTitle = await screen.findByText("Delete API key");
    const confirmDialog = confirmTitle.closest("[role='alertdialog']");
    expect(confirmDialog).not.toBeNull();
    await user.click(
      within(confirmDialog as HTMLElement).getByRole("button", { name: "Delete" }),
    );

    await waitFor(() => {
      expect(screen.queryByText(updatedName)).not.toBeInTheDocument();
    });
  });
});
