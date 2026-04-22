import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import App from "@/App";
import { AutomationsPage } from "@/features/automations/components/automations-page";
import { renderWithProviders } from "@/test/utils";

function getJobRow(jobName: string): HTMLElement {
	const cell = screen.getByText(jobName);
	const row = cell.closest("tr");
	if (!row) {
		throw new Error(`Row for job '${jobName}' not found`);
	}
	return row;
}

describe("automations page integration", () => {
	beforeEach(() => {
		window.history.pushState({}, "", "/automations");
	});

	it("navigates to automations from the header navigation", async () => {
		const user = userEvent.setup({ delay: null });
		window.history.pushState({}, "", "/dashboard");
		renderWithProviders(<App />);

		await user.click(await screen.findByRole("link", { name: "Automations" }));

		expect(await screen.findByRole("heading", { name: "Automations" })).toBeInTheDocument();
		expect(window.location.pathname).toBe("/automations");
	});

	it("validates form input, creates a job, updates it, and renders run history", async () => {
		const user = userEvent.setup({ delay: null });
		renderWithProviders(<AutomationsPage />);

		expect(await screen.findByRole("heading", { name: "Automations" })).toBeInTheDocument();
		expect(await screen.findByText("No automations")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Add automation" }));

		const createDialog = await screen.findByRole("dialog", { name: "Add automation" });
		expect(within(createDialog).getByRole("heading", { name: "Basics" })).toBeInTheDocument();
		expect(within(createDialog).getByRole("heading", { name: "Schedule" })).toBeInTheDocument();
		expect(within(createDialog).getByRole("heading", { name: "Content / Execution" })).toBeInTheDocument();

		await user.type(within(createDialog).getByPlaceholderText("Automation name"), "Daily smoke ping");
		await user.click(within(createDialog).getByRole("button", { name: "Accounts" }));
		const accountsMenu = await screen.findByRole("menu");
		await user.click(
			within(accountsMenu).getByRole("menuitemcheckbox", { name: /primary@example\.com/i }),
		);
		await user.keyboard("{Escape}");
		await waitFor(() => {
			expect(screen.queryByRole("menu")).not.toBeInTheDocument();
		});

		await user.click(within(createDialog).getByRole("button", { name: "Create automation" }));
		await waitFor(() => {
			expect(screen.queryByRole("dialog", { name: "Add automation" })).not.toBeInTheDocument();
		});

		expect(await screen.findByText("Daily smoke ping")).toBeInTheDocument();
		expect(screen.getByText("Runs will appear here after automation jobs execute.")).toBeInTheDocument();

		await user.click(within(getJobRow("Daily smoke ping")).getByRole("switch"));
		await waitFor(() => {
			expect(within(getJobRow("Daily smoke ping")).getByText("Disabled")).toBeInTheDocument();
		});

		await user.click(within(getJobRow("Daily smoke ping")).getByRole("button", { name: "Edit Daily smoke ping" }));
		const editDialog = await screen.findByRole("dialog", { name: "Edit automation" });
		const nameInput = within(editDialog).getByLabelText("Name");
		await user.clear(nameInput);
		await user.type(nameInput, "Daily smoke ping edited");
		await user.click(within(editDialog).getByRole("button", { name: "Save changes" }));
		await waitFor(() => {
			expect(screen.queryByRole("dialog", { name: "Edit automation" })).not.toBeInTheDocument();
		});
		expect(await screen.findByText("Daily smoke ping edited")).toBeInTheDocument();

		await user.click(within(getJobRow("Daily smoke ping edited")).getByRole("button", { name: "Run now Daily smoke ping edited" }));
		const runNowDialog = await screen.findByRole("alertdialog", { name: "Run automation now" });
		await user.click(within(runNowDialog).getByRole("button", { name: "Run now" }));
		await waitFor(() => {
			expect(screen.queryByRole("alertdialog", { name: "Run automation now" })).not.toBeInTheDocument();
		});

		const recentRunsSection = screen.getByRole("heading", { name: "Recent runs" }).closest("section");
		if (!recentRunsSection) {
			throw new Error("Recent runs section not found");
		}
		expect(await within(recentRunsSection).findByText("manual")).toBeInTheDocument();
		expect(await within(recentRunsSection).findByText("success")).toBeInTheDocument();
		await waitFor(() => {
			expect(screen.queryByText("Runs will appear here after automation jobs execute.")).not.toBeInTheDocument();
		});
	});

	it("creates automation with default all-accounts selection", async () => {
		const user = userEvent.setup({ delay: null });
		renderWithProviders(<AutomationsPage />);

		expect(await screen.findByRole("heading", { name: "Automations" })).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Add automation" }));
		const createDialog = await screen.findByRole("dialog", { name: "Add automation" });
		await user.type(within(createDialog).getByPlaceholderText("Automation name"), "All accounts job");
		await user.click(within(createDialog).getByRole("button", { name: "Create automation" }));
		await waitFor(() => {
			expect(screen.queryByRole("dialog", { name: "Add automation" })).not.toBeInTheDocument();
		});

		const row = getJobRow("All accounts job");
		expect(within(row).getByText("All accounts")).toBeInTheDocument();
		expect(screen.queryByText("No accounts available. Add at least one account.")).not.toBeInTheDocument();
	});
});
