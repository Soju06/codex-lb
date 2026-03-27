import { HttpResponse, http } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import App from "@/App";
import { createApiKey, createApiKeyUsage7Day } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

function getDialogFooterClose(dialog: HTMLElement): HTMLElement {
	return within(dialog)
		.getAllByRole("button", { name: "Close" })
		.find((button) => button.getAttribute("data-slot") === "button") as HTMLElement;
}

describe("apis page integration", () => {
	beforeEach(() => {
		window.history.pushState({}, "", "/apis");
	});

	it("loads the APIs page, selects by query param, and filters keys by search", async () => {
		window.history.pushState({}, "", "/apis?selected=key_2");
		const user = userEvent.setup();
		renderWithProviders(<App />);

		expect(await screen.findByRole("heading", { name: "APIs" })).toBeInTheDocument();
		expect(await screen.findByRole("heading", { name: "Read only key" })).toBeInTheDocument();

		const search = screen.getByPlaceholderText("Search API keys...");
		await user.type(search, "Default");

		expect(screen.getByText("Default key")).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /Default key/i }));
		expect(await screen.findByRole("heading", { name: "Default key" })).toBeInTheDocument();
	});

	it("creates a key, shows the one-time key dialog, and refreshes the list", async () => {
		const user = userEvent.setup();
		renderWithProviders(<App />);

		await user.click(await screen.findByRole("button", { name: "Create API Key" }));
		const createDialog = await screen.findByRole("dialog", { name: "Create API key" });
		await user.type(within(createDialog).getByLabelText("Name"), "Created from APIs page");
		await user.click(within(createDialog).getByRole("button", { name: "Create" }));

		const dialog = await screen.findByRole("dialog", { name: "API key created" });
		expect(within(dialog).getByText(/sk-test-generated-/)).toBeInTheDocument();
		expect(within(dialog).getByText(/It will not be shown again/)).toBeInTheDocument();

		await user.click(getDialogFooterClose(dialog));
		expect(await screen.findByText("Created from APIs page")).toBeInTheDocument();
	});

	it("edits, toggles, regenerates, and deletes the selected key", async () => {
		const user = userEvent.setup();
		renderWithProviders(<App />);

		expect(await screen.findByRole("heading", { name: "Default key" })).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Actions" }));
		await user.click(screen.getByRole("menuitem", { name: "Edit" }));

		const editDialog = await screen.findByRole("dialog", { name: "Edit API key" });
		const nameInput = within(editDialog).getByLabelText("Name");
		await user.clear(nameInput);
		await user.type(nameInput, "Updated from APIs page");
		await user.click(within(editDialog).getByRole("button", { name: "Save" }));

		expect(await screen.findByRole("heading", { name: "Updated from APIs page" })).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Disable" }));
		expect(await screen.findByRole("button", { name: "Enable" })).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Actions" }));
		await user.click(screen.getByRole("menuitem", { name: "Regenerate" }));

		const regeneratedDialog = await screen.findByRole("dialog", { name: "API key created" });
		expect(within(regeneratedDialog).getByText(/sk-test-regenerated-key_1/)).toBeInTheDocument();
		await user.click(getDialogFooterClose(regeneratedDialog));

		await user.click(screen.getByRole("button", { name: "Delete" }));
		const confirmDialog = await screen.findByRole("alertdialog", { name: "Delete API key" });
		await user.click(within(confirmDialog).getByRole("button", { name: "Delete" }));

		await waitFor(() => {
			expect(screen.queryByText("Updated from APIs page")).not.toBeInTheDocument();
		});
	});

	it("shows backend errors from API mutations in the page alert", async () => {
		const user = userEvent.setup();
		server.use(
			http.post("/api/api-keys/", () => {
				return HttpResponse.json(
					{ error: { code: "invalid_api_key_payload", message: "Invalid create payload" } },
					{ status: 400 },
				);
			}),
		);

		renderWithProviders(<App />);

		await user.click(await screen.findByRole("button", { name: "Create API Key" }));
		const createDialog = await screen.findByRole("dialog", { name: "Create API key" });
		await user.type(within(createDialog).getByLabelText("Name"), "Broken create");
		await user.click(within(createDialog).getByRole("button", { name: "Create" }));

		expect(await screen.findByText("Invalid create payload")).toBeInTheDocument();
	});

	it("renders the empty detail state when the API list is empty", async () => {
		server.use(
			http.get("/api/api-keys/", () => HttpResponse.json([])),
		);

		renderWithProviders(<App />);

		expect(await screen.findByRole("heading", { name: "APIs" })).toBeInTheDocument();
		expect(await screen.findByText("No matching API keys")).toBeInTheDocument();
		expect(screen.getByText("Select an API key")).toBeInTheDocument();
	});

	it("shows the correct detail view for trend and usage responses returned by the API", async () => {
		server.use(
			http.get("/api/api-keys/", () =>
				HttpResponse.json([
					createApiKey({
						id: "key_custom",
						name: "Custom analytics key",
						allowedModels: null,
						expiresAt: null,
						usageSummary: {
							requestCount: 42,
							totalTokens: 12_000,
							cachedInputTokens: 3_000,
							totalCostUsd: 0.42,
						},
					}),
				]),
			),
			http.get("/api/api-keys/:keyId/trends", ({ params }) => {
				return HttpResponse.json({
					keyId: String(params.keyId),
					cost: [
						{ t: "2026-01-01T00:00:00Z", v: 0.12 },
						{ t: "2026-01-01T01:00:00Z", v: 0.3 },
					],
					tokens: [
						{ t: "2026-01-01T00:00:00Z", v: 5000 },
						{ t: "2026-01-01T01:00:00Z", v: 7000 },
					],
				});
			}),
			http.get("/api/api-keys/:keyId/usage-7d", ({ params }) => {
				return HttpResponse.json(createApiKeyUsage7Day({ keyId: String(params.keyId) }));
			}),
		);

		renderWithProviders(<App />);

		expect(await screen.findByRole("heading", { name: "Custom analytics key" })).toBeInTheDocument();
		expect(screen.getByText("All models")).toBeInTheDocument();
		expect(screen.getByText(/12K tok/)).toBeInTheDocument();
		expect(screen.getByText(/3K cached/)).toBeInTheDocument();
		expect(screen.getByText(/42 req/)).toBeInTheDocument();
		expect(screen.getByText(/\$0.42/)).toBeInTheDocument();
	});
});
