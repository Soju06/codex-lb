import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

function getParentRow(cell: HTMLElement): HTMLElement {
  const row = cell.closest("tr");
  if (!row) throw new Error("Expected element to be inside a table row");
  return row;
}

async function openRowActions(user: ReturnType<typeof userEvent.setup>, row: HTMLElement) {
  const actionsButton = within(row).getByRole("button", { name: "Actions" });
  await user.click(actionsButton);
}

describe("api keys flow integration", () => {
  it("creates, shows plain key dialog, edits, and deletes an api key", async () => {
    const user = userEvent.setup();
    const createdName = "Integration Key";
    const updatedName = "Integration Key Updated";

    window.history.pushState({}, "", "/settings");
    renderWithProviders(<App />);

    const createButton = await screen.findByRole("button", { name: "Create key" });
    expect(createButton).toBeInTheDocument();
    await user.click(createButton);
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

    await openRowActions(user, createdRow);
    await user.click(await screen.findByRole("menuitem", { name: /Edit/ }));
    const nameInput = await screen.findByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, updatedName);
    await user.click(screen.getByRole("button", { name: "Save" }));

    const updatedRow = getParentRow(await screen.findByText(updatedName));

    await openRowActions(user, updatedRow);
    await user.click(await screen.findByRole("menuitem", { name: /Delete/ }));
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

  it("creates an api key with assigned accounts", async () => {
    const user = userEvent.setup();

    window.history.pushState({}, "", "/settings");
    renderWithProviders(<App />);

    await user.click(await screen.findByRole("button", { name: "Create key" }));
    await user.type(screen.getByLabelText("Name"), "Scoped Integration Key");
    await user.click(await screen.findByRole("button", { name: "All accounts" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: /primary@example\.com/i }));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Create" }));

    const createdDialog = await screen.findByRole("dialog", { name: "API key created" });
    const closeCandidates = within(createdDialog).getAllByRole("button", {
      name: "Close",
    });
    const closeButton =
      closeCandidates.find((element) => element.getAttribute("data-slot") === "button") ??
      closeCandidates[0];
    await user.click(closeButton);

    const createdRow = getParentRow(await screen.findByText("Scoped Integration Key"));
    await openRowActions(user, createdRow);
    await user.click(await screen.findByRole("menuitem", { name: /Edit/ }));

    expect(await screen.findByRole("button", { name: "1 account selected" })).toBeInTheDocument();
  });

  it("creates and updates an api key restricted to image models", async () => {
    server.use(
      http.get("/api/models", () =>
        HttpResponse.json({
          models: [
            { id: "gpt-5.1", name: "GPT 5.1" },
            {
              id: "gpt-image-2",
              name: "gpt-image-2",
              sourceOnly: false,
              imageOnly: true,
              supportedReasoningEfforts: [],
              defaultReasoningEffort: null,
            },
            {
              id: "gpt-image-1.5",
              name: "gpt-image-1.5",
              sourceOnly: false,
              imageOnly: true,
              supportedReasoningEfforts: [],
              defaultReasoningEffort: null,
            },
            {
              id: "gpt-image-1",
              name: "gpt-image-1",
              sourceOnly: false,
              imageOnly: true,
              supportedReasoningEfforts: [],
              defaultReasoningEffort: null,
            },
            {
              id: "gpt-image-1-mini",
              name: "gpt-image-1-mini",
              sourceOnly: false,
              imageOnly: true,
              supportedReasoningEfforts: [],
              defaultReasoningEffort: null,
            },
          ],
        }),
      ),
    );
    const user = userEvent.setup({ delay: null });
    const keyName = "Image API Key";

    window.history.pushState({}, "", "/settings");
    renderWithProviders(<App />);

    const createButton = await screen.findByRole("button", { name: "Create key" });
    await waitFor(() => expect(createButton).toBeEnabled());
    await user.click(createButton);
    const createDialog = (await screen.findByText("Create API key")).closest("[role='dialog']");
    expect(createDialog).not.toBeNull();
    if (!createDialog) throw new Error("Expected create API key dialog");
    const createDialogElement = createDialog as HTMLElement;
    await user.type(within(createDialogElement).getByLabelText("Name"), keyName);
    await user.click(within(createDialogElement).getByRole("button", { name: "All models" }));
    const createModelMenu = await screen.findByRole("menu");
    await user.type(within(createModelMenu).getByPlaceholderText("Search"), "gpt-image");
    expect(within(createModelMenu).getByText("gpt-image-2")).toBeInTheDocument();
    expect(within(createModelMenu).getByText("gpt-image-1.5")).toBeInTheDocument();
    expect(within(createModelMenu).getByText("gpt-image-1")).toBeInTheDocument();
    expect(within(createModelMenu).getByText("gpt-image-1-mini")).toBeInTheDocument();
    await user.click(
      within(createModelMenu).getByRole("menuitemcheckbox", { name: "gpt-image-2" }),
    );
    await user.keyboard("{Escape}");
    await user.click(within(createDialogElement).getByRole("button", { name: "Create" }));

    const createdDialog = await screen.findByRole("dialog", { name: "API key created" });
    const closeButton = within(createdDialog)
      .getAllByRole("button", { name: "Close" })
      .find((element) => element.getAttribute("data-slot") === "button");
    expect(closeButton).toBeDefined();
    if (!closeButton) throw new Error("Expected created API key close button");
    await user.click(closeButton);

    const createdRow = getParentRow(await screen.findByText(keyName));
    expect(within(createdRow).getByText("gpt-image-2")).toBeInTheDocument();
    await openRowActions(user, createdRow);
    await user.click(await screen.findByRole("menuitem", { name: /Edit/ }));

    const firstEditDialog = await screen.findByRole("dialog", { name: "Edit API key" });
    expect(within(firstEditDialog).getByText("gpt-image-2")).toBeInTheDocument();
    await user.click(within(firstEditDialog).getByRole("button", { name: "1 model selected" }));
    const editModelMenu = await screen.findByRole("menu");
    await user.type(within(editModelMenu).getByPlaceholderText("Search"), "gpt-image");
    await user.click(
      within(editModelMenu).getByRole("menuitemcheckbox", { name: "gpt-image-2" }),
    );
    await user.click(
      within(editModelMenu).getByRole("menuitemcheckbox", { name: "gpt-image-1-mini" }),
    );
    await user.keyboard("{Escape}");
    await user.click(within(firstEditDialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(within(createdRow).getByText("gpt-image-1-mini")).toBeInTheDocument();
    });
    await openRowActions(user, createdRow);
    await user.click(await screen.findByRole("menuitem", { name: /Edit/ }));

    const secondEditDialog = await screen.findByRole("dialog", { name: "Edit API key" });
    expect(within(secondEditDialog).getByText("gpt-image-1-mini")).toBeInTheDocument();
    expect(within(secondEditDialog).queryByText("gpt-image-2")).not.toBeInTheDocument();
  }, 30_000);

  it("displays the current api key list on settings", async () => {
    window.history.pushState({}, "", "/settings");
    renderWithProviders(<App />);

    expect(await screen.findByRole("columnheader", { name: "Name" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Prefix" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Models" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Usage" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Expiry" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Status" })).toBeInTheDocument();

    const defaultKeyRow = getParentRow(screen.getByText("Default key"));
    expect(within(defaultKeyRow).getByText("sk-test")).toBeInTheDocument();
    expect(within(defaultKeyRow).getByText("gpt-5.1")).toBeInTheDocument();
    expect(within(defaultKeyRow).getByText("Tokens: 125K/1M weekly")).toBeInTheDocument();
    expect(within(defaultKeyRow).getByText("Active")).toBeInTheDocument();

    const readOnlyRow = getParentRow(screen.getByText("Read only key"));
    expect(within(readOnlyRow).getByText("sk-second")).toBeInTheDocument();
    expect(within(readOnlyRow).getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(within(readOnlyRow).getByText(/12\.5K tok/)).toBeInTheDocument();
    expect(within(readOnlyRow).getByText(/2\.2K cached/)).toBeInTheDocument();
    expect(within(readOnlyRow).getByText(/42 req/)).toBeInTheDocument();
    expect(within(readOnlyRow).getByText(/\$0\.42/)).toBeInTheDocument();
    expect(within(readOnlyRow).getByText("Never")).toBeInTheDocument();
    expect(within(readOnlyRow).getByText("Disabled")).toBeInTheDocument();
  });

  it("shows usage bars when editing a key with limits", async () => {
    const user = userEvent.setup({ delay: null });

    window.history.pushState({}, "", "/settings");
    renderWithProviders(<App />);

    expect(await screen.findByText("Default key")).toBeInTheDocument();
    const defaultKeyRow = getParentRow(screen.getByText("Default key"));
    await openRowActions(user, defaultKeyRow);
    await user.click(await screen.findByRole("menuitem", { name: /Edit/ }));

    // Edit dialog should show current usage section
    expect(await screen.findByText("Current usage")).toBeInTheDocument();
  });
});
