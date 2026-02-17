import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { renderWithProviders } from "@/test/utils";

function getParentRow(cell: HTMLElement): HTMLElement {
  const row = cell.closest("tr");
  if (!row) throw new Error("Expected element to be inside a table row");
  return row;
}

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

    const createdDialog = await screen.findByRole("dialog", { name: "API key created" });
    expect(screen.getByText(/sk-test-generated/i)).toBeInTheDocument();
    const closeCandidates = within(createdDialog).getAllByRole("button", {
      name: "Close",
    });
    const closeButton =
      closeCandidates.find((element) => element.getAttribute("data-slot") === "button") ??
      closeCandidates[0];
    await user.click(closeButton);

    const createdRow = getParentRow(await screen.findByText(createdName));

    await user.click(within(createdRow).getByRole("button", { name: "Edit" }));
    const nameInput = await screen.findByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, updatedName);
    await user.click(screen.getByRole("button", { name: "Save" }));

    const updatedRow = getParentRow(await screen.findByText(updatedName));

    await user.click(within(updatedRow).getByRole("button", { name: "Delete" }));
    const confirmTitle = await screen.findByText("Delete API key");
    const confirmDialog = confirmTitle.closest("[role='alertdialog']");
    expect(confirmDialog).not.toBeNull();
    if (!confirmDialog) throw new Error("Expected confirm dialog");
    await user.click(
      within(confirmDialog as HTMLElement).getByRole("button", { name: "Delete" }),
    );

    await waitFor(() => {
      expect(screen.queryByText(updatedName)).not.toBeInTheDocument();
    });
  });

  it("displays existing api keys with limit summaries", async () => {
    window.history.pushState({}, "", "/settings");
    renderWithProviders(<App />);

    // Default mock keys are loaded — first key has a total_tokens weekly limit
    expect(await screen.findByText("Default key")).toBeInTheDocument();
    expect(await screen.findByText("Read only key")).toBeInTheDocument();

    // Verify limit summary is displayed for the first key (has limits)
    const defaultKeyRow = getParentRow(screen.getByText("Default key"));
    expect(within(defaultKeyRow).getByText(/Tokens/)).toBeInTheDocument();
  });

  it("shows limit rules editor in create dialog with basic mode", async () => {
    const user = userEvent.setup();

    window.history.pushState({}, "", "/settings");
    renderWithProviders(<App />);

    expect(await screen.findByText("API Keys")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create key" }));

    // Basic mode: should show weekly token limit and weekly cost limit inputs
    expect(screen.getByText("Limits")).toBeInTheDocument();
    expect(screen.getByText("Weekly token limit")).toBeInTheDocument();
    expect(screen.getByText("Weekly cost limit ($)")).toBeInTheDocument();

    // Should also show "Allowed models" label
    expect(screen.getByText("Allowed models")).toBeInTheDocument();
  });

  it("shows usage bars when editing a key with limits", async () => {
    const user = userEvent.setup();

    window.history.pushState({}, "", "/settings");
    renderWithProviders(<App />);

    expect(await screen.findByText("Default key")).toBeInTheDocument();
    const defaultKeyRow = getParentRow(screen.getByText("Default key"));
    await user.click(within(defaultKeyRow).getByRole("button", { name: "Edit" }));

    // Edit dialog should show current usage section
    expect(await screen.findByText("Current usage")).toBeInTheDocument();
  });
});
