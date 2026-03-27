import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { createApiKey } from "@/test/mocks/factories";
import { renderWithProviders } from "@/test/utils";

import { ApiDetail } from "./api-detail";

const defaultCallbacks = {
	busy: false,
	onEdit: vi.fn(),
	onDelete: vi.fn(),
	onRegenerate: vi.fn(),
	onToggleActive: vi.fn(),
};

describe("ApiDetail", () => {
	it("renders empty state when no key is selected", () => {
		renderWithProviders(<ApiDetail apiKey={null} {...defaultCallbacks} />);

		expect(screen.getByText("Select an API key")).toBeInTheDocument();
		expect(
			screen.getByText("Choose an API key from the list to view details."),
		).toBeInTheDocument();
	});

	it("renders key name and chart section", () => {
		const apiKey = createApiKey({ name: "Test Key" });
		renderWithProviders(
			<ApiDetail
				apiKey={apiKey}
				trends={null}
				usage7Day={null}
				{...defaultCallbacks}
			/>,
		);

		expect(screen.getByText("Test Key")).toBeInTheDocument();
		expect(screen.getByText("Tokens")).toBeInTheDocument();
		expect(screen.getByText("Cost")).toBeInTheDocument();
		expect(screen.getByText("Accumulated")).toBeInTheDocument();
	});

	it("renders Disable button for active key and Enable for inactive", () => {
		const activeKey = createApiKey({ isActive: true });
		const { unmount } = renderWithProviders(
			<ApiDetail
				apiKey={activeKey}
				trends={null}
				usage7Day={null}
				{...defaultCallbacks}
			/>,
		);

		expect(screen.getByRole("button", { name: "Disable" })).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Enable" }),
		).not.toBeInTheDocument();

		unmount();

		const inactiveKey = createApiKey({ isActive: false });
		renderWithProviders(
			<ApiDetail
				apiKey={inactiveKey}
				trends={null}
				usage7Day={null}
				{...defaultCallbacks}
			/>,
		);

		expect(screen.getByRole("button", { name: "Enable" })).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Disable" }),
		).not.toBeInTheDocument();
	});

	it("renders Delete button", () => {
		const apiKey = createApiKey();
		renderWithProviders(
			<ApiDetail
				apiKey={apiKey}
				trends={null}
				usage7Day={null}
				{...defaultCallbacks}
			/>,
		);

		expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
	});

	it("calls onToggleActive when Disable is clicked", async () => {
		const user = userEvent.setup();
		const onToggleActive = vi.fn();
		const apiKey = createApiKey({ isActive: true });

		renderWithProviders(
			<ApiDetail
				apiKey={apiKey}
				trends={null}
				usage7Day={null}
				{...defaultCallbacks}
				onToggleActive={onToggleActive}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Disable" }));
		expect(onToggleActive).toHaveBeenCalledWith(apiKey);
	});

	it("calls onDelete when Delete is clicked", async () => {
		const user = userEvent.setup();
		const onDelete = vi.fn();
		const apiKey = createApiKey();

		renderWithProviders(
			<ApiDetail
				apiKey={apiKey}
				trends={null}
				usage7Day={null}
				{...defaultCallbacks}
				onDelete={onDelete}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Delete" }));
		expect(onDelete).toHaveBeenCalledWith(apiKey);
	});

	it("renders action buttons", () => {
		const apiKey = createApiKey({ isActive: true });
		renderWithProviders(
			<ApiDetail
				apiKey={apiKey}
				trends={null}
				usage7Day={null}
				busy
				{...defaultCallbacks}
			/>,
		);

		expect(screen.getByRole("button", { name: "Disable" })).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
	});

	it("toggles Accumulated switch", async () => {
		const user = userEvent.setup();
		const apiKey = createApiKey();

		renderWithProviders(
			<ApiDetail
				apiKey={apiKey}
				trends={null}
				usage7Day={null}
				{...defaultCallbacks}
			/>,
		);

		const sw = screen.getByRole("switch");
		expect(sw).toBeInTheDocument();
		expect(sw).not.toBeChecked();

		await user.click(sw);
		expect(sw).toBeChecked();
	});

	it("renders dropdown menu with Edit and Regenerate options", async () => {
		const user = userEvent.setup();
		const apiKey = createApiKey();

		renderWithProviders(
			<ApiDetail
				apiKey={apiKey}
				trends={null}
				usage7Day={null}
				{...defaultCallbacks}
			/>,
		);

		const trigger = screen.getByRole("button", { name: "Actions" });
		await user.click(trigger);

		expect(screen.getByRole("menuitem", { name: "Edit" })).toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Regenerate" }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("menuitem", { name: "Delete" }),
		).not.toBeInTheDocument();
	});
});
