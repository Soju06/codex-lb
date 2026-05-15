import type { ComponentProps } from "react";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePrivacyStore } from "@/hooks/use-privacy";
import {
	createApiKey,
	createApiKeyAccountUsage7Day,
	createApiKeyTrends,
	createApiKeyUsage7Day,
} from "@/test/mocks/factories";
import { renderWithProviders } from "@/test/utils";

import { ApiDetail } from "./api-detail";

const callbacks = {
	onEdit: vi.fn(),
	onDelete: vi.fn(),
	onRegenerate: vi.fn(),
	onToggleActive: vi.fn(),
};

beforeEach(() => {
	vi.clearAllMocks();
	usePrivacyStore.setState({ blurred: false });
});

function renderApiDetail(overrides: Partial<ComponentProps<typeof ApiDetail>> = {}) {
	const apiKey = createApiKey({ name: "Analytics Key" });
	return renderWithProviders(
		<ApiDetail
			apiKey={apiKey}
			trends={null}
			usage7Day={null}
			usage7DayLoading={false}
			usage7DayError={null}
			accountUsage7Day={null}
			accountUsage7DayLoading={false}
			accountUsage7DayError={null}
			busy={false}
			{...callbacks}
			{...overrides}
		/>,
	);
}

describe("ApiDetail", () => {
	it("renders the empty state when no key is selected", () => {
		renderWithProviders(
			<ApiDetail
				apiKey={null}
				trends={null}
				usage7Day={null}
				usage7DayLoading={false}
				usage7DayError={null}
				accountUsage7Day={null}
				accountUsage7DayLoading={false}
				accountUsage7DayError={null}
				busy={false}
				{...callbacks}
			/>,
		);

		expect(screen.getByText("Select an API key")).toBeInTheDocument();
		expect(screen.getByText("Choose an API key from the list to view details.")).toBeInTheDocument();
	});

	it("shows trend chart controls and key details for the selected key", () => {
		renderApiDetail({
			trends: createApiKeyTrends({
				cost: [
					{ t: "2026-01-01T00:00:00Z", v: 0.12 },
					{ t: "2026-01-01T01:00:00Z", v: 0.08 },
				],
				tokens: [
					{ t: "2026-01-01T00:00:00Z", v: 1200 },
					{ t: "2026-01-01T01:00:00Z", v: 800 },
				],
			}),
		});

		expect(screen.getByRole("heading", { name: "Analytics Key" })).toBeInTheDocument();
		expect(screen.getByRole("heading", { name: "Usage trend" })).toBeInTheDocument();
		expect(screen.getByText("Last 7 days by tokens and cost.")).toBeInTheDocument();
		expect(screen.getByText("Tokens")).toBeInTheDocument();
		expect(screen.getByText("Cost")).toBeInTheDocument();
		expect(screen.getByRole("switch")).toBeInTheDocument();
		expect(screen.getByText("Key Details")).toBeInTheDocument();
	});

	it("prefers the 7 day usage payload over list summary usage", () => {
		renderApiDetail({
			apiKey: createApiKey({
				usageSummary: {
					requestCount: 1,
					totalTokens: 15,
					cachedInputTokens: 0,
					totalCostUsd: 0.01,
				},
			}),
			usage7Day: createApiKeyUsage7Day({
				totalTokens: 280_000,
				cachedInputTokens: 45_000,
				totalRequests: 350,
				totalCostUsd: 2.47,
			}),
		});

		expect(screen.getByText(/280K tok/)).toBeInTheDocument();
		expect(screen.getByText(/45K cached/)).toBeInTheDocument();
		expect(screen.getByText(/350 req/)).toBeInTheDocument();
		expect(screen.getByText(/\$2.47/)).toBeInTheDocument();
	});

	it("renders account cost donut labels in cost order with unknown last", () => {
		renderApiDetail({
			accountUsage7Day: createApiKeyAccountUsage7Day({
				accounts: [
					{
						accountId: "acc_low",
						displayName: "Low Account",
						totalCostUsd: 1.25,
						totalTokens: 1000,
						totalRequests: 4,
					},
					{
						accountId: null,
						displayName: "Unknown Account",
						totalCostUsd: 99,
						totalTokens: 9000,
						totalRequests: 9,
					},
					{
						accountId: "acc_high",
						displayName: "High Account",
						totalCostUsd: 3.5,
						totalTokens: 3000,
						totalRequests: 8,
					},
				],
			}),
		});

		expect(screen.getByTestId("api-account-cost-donut")).toBeInTheDocument();
		expect(screen.getByText("High Account")).toBeInTheDocument();
		expect(screen.getByText("Low Account")).toBeInTheDocument();
		expect(screen.getByText("Unknown Account")).toBeInTheDocument();
		expect(screen.getByTestId("api-account-cost-legend-0")).toHaveTextContent("High Account");
		expect(screen.getByTestId("api-account-cost-legend-1")).toHaveTextContent("Low Account");
		expect(screen.getByTestId("api-account-cost-legend-2")).toHaveTextContent("Unknown Account");
		expect(screen.getByTestId("api-account-cost-legend-2")).toHaveTextContent("$99.00");
	});

	it("keeps deleted and unknown account cost buckets distinct and styles deleted like dashboard used", () => {
		renderApiDetail({
			accountUsage7Day: createApiKeyAccountUsage7Day({
				accounts: [
					{
						accountId: null,
						displayName: "Deleted Accounts",
						totalCostUsd: 4.5,
						totalTokens: 4500,
						totalRequests: 12,
					},
					{
						accountId: null,
						displayName: "Unknown Account",
						totalCostUsd: 2.25,
						totalTokens: 2250,
						totalRequests: 6,
					},
				],
			}),
		});

		const deletedLegend = screen.getByTestId("api-account-cost-legend-0");
		const unknownLegend = screen.getByTestId("api-account-cost-legend-1");
		const deletedDot = deletedLegend.querySelector("span[aria-hidden='true']");
		const unknownDot = unknownLegend.querySelector("span[aria-hidden='true']");
		expect(deletedLegend).toHaveTextContent("Deleted Accounts");
		expect(deletedLegend).toHaveTextContent("$4.50");
		expect(unknownLegend).toHaveTextContent("Unknown Account");
		expect(unknownLegend).toHaveTextContent("$2.25");
		expect(deletedDot).toHaveStyle({ backgroundColor: "rgb(211, 211, 211)" });
		expect(unknownDot).not.toHaveStyle({ backgroundColor: "rgb(211, 211, 211)" });
	});


	it("limits the account cost legend to three rows", () => {
		renderApiDetail({
			accountUsage7Day: createApiKeyAccountUsage7Day({
				accounts: [
					{
						accountId: "acc_one",
						displayName: "One Account",
						totalCostUsd: 4,
						totalTokens: 4000,
						totalRequests: 8,
					},
					{
						accountId: "acc_two",
						displayName: "Two Account",
						totalCostUsd: 3,
						totalTokens: 3000,
						totalRequests: 6,
					},
					{
						accountId: "acc_three",
						displayName: "Three Account",
						totalCostUsd: 2,
						totalTokens: 2000,
						totalRequests: 4,
					},
					{
						accountId: "acc_four",
						displayName: "Four Account",
						totalCostUsd: 1,
						totalTokens: 1000,
						totalRequests: 2,
					},
				],
			}),
		});

		expect(screen.queryByTestId("api-account-cost-legend-3")).not.toBeInTheDocument();
		expect(screen.queryByText("Four Account")).not.toBeInTheDocument();
	});

	it("applies privacy blur to email account names in the account cost legend", () => {
		usePrivacyStore.setState({ blurred: true });

		const { container } = renderApiDetail({
			accountUsage7Day: createApiKeyAccountUsage7Day({
				accounts: [
					{
						accountId: "acc_email",
						displayName: "owner@example.com",
						totalCostUsd: 4.2,
						totalTokens: 4000,
						totalRequests: 10,
					},
				],
			}),
		});

		expect(screen.getByTestId("api-account-cost-legend-0")).toHaveTextContent("owner@example.com");
		expect(container.querySelector(".privacy-blur")).not.toBeNull();
	});

	it("does not fall back to list summary usage while the 7 day query is loading", () => {
		renderApiDetail({
			apiKey: createApiKey({
				usageSummary: {
					requestCount: 1,
					totalTokens: 15,
					cachedInputTokens: 0,
					totalCostUsd: 0.01,
				},
			}),
			usage7Day: null,
			usage7DayLoading: true,
		});

		expect(screen.getByText("Loading 7-day usage...")).toBeInTheDocument();
		expect(screen.queryByText(/15 tok/)).not.toBeInTheDocument();
		expect(screen.queryByText(/1 req/)).not.toBeInTheDocument();
	});

	it("shows a usage error instead of falling back to list summary usage", () => {
		renderApiDetail({
			apiKey: createApiKey({
				usageSummary: {
					requestCount: 1,
					totalTokens: 15,
					cachedInputTokens: 0,
					totalCostUsd: 0.01,
				},
			}),
			usage7Day: null,
			usage7DayError: "boom usage",
		});

		expect(screen.getByText("boom usage")).toBeInTheDocument();
		expect(screen.getByText("7-day usage unavailable")).toBeInTheDocument();
		expect(screen.queryByText(/15 tok/)).not.toBeInTheDocument();
	});

	it("keeps the accumulated toggle interactive when trend data is present", async () => {
		const user = userEvent.setup();
		renderApiDetail({
			trends: createApiKeyTrends({
				cost: [{ t: "2026-01-01T00:00:00Z", v: 0.2 }],
				tokens: [{ t: "2026-01-01T00:00:00Z", v: 1500 }],
			}),
		});

		const toggle = screen.getByRole("switch");
		expect(toggle).not.toBeChecked();

		await user.click(toggle);
		expect(toggle).toBeChecked();
	});

	it("shows enable action for inactive keys and disable action for active keys", () => {
		const { rerender } = renderWithProviders(
			<ApiDetail
				apiKey={createApiKey({ isActive: true })}
				trends={null}
				usage7Day={null}
				usage7DayLoading={false}
				usage7DayError={null}
				accountUsage7Day={null}
				accountUsage7DayLoading={false}
				accountUsage7DayError={null}
				busy={false}
				{...callbacks}
			/>,
		);

		expect(screen.getByRole("button", { name: "Disable" })).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Enable" })).not.toBeInTheDocument();

		rerender(
			<ApiDetail
				apiKey={createApiKey({ isActive: false })}
				trends={null}
				usage7Day={null}
				usage7DayLoading={false}
				usage7DayError={null}
				accountUsage7Day={null}
				accountUsage7DayLoading={false}
				accountUsage7DayError={null}
				busy={false}
				{...callbacks}
			/>,
		);

		expect(screen.getByRole("button", { name: "Enable" })).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Disable" })).not.toBeInTheDocument();
	});

	it("invokes toggle and delete callbacks from footer actions", async () => {
		const user = userEvent.setup();
		const apiKey = createApiKey({ isActive: true });
		const onToggleActive = vi.fn();
		const onDelete = vi.fn();

		renderApiDetail({ apiKey, onToggleActive, onDelete });

		await user.click(screen.getByRole("button", { name: "Disable" }));
		await user.click(screen.getByRole("button", { name: "Delete" }));

		expect(onToggleActive).toHaveBeenCalledWith(apiKey);
		expect(onDelete).toHaveBeenCalledWith(apiKey);
	});

	it("opens the actions menu and routes edit and regenerate actions", async () => {
		const user = userEvent.setup();
		const apiKey = createApiKey();
		const onEdit = vi.fn();
		const onRegenerate = vi.fn();

		renderApiDetail({ apiKey, onEdit, onRegenerate });

		await user.click(screen.getByRole("button", { name: "Actions" }));
		await user.click(screen.getByRole("menuitem", { name: "Edit" }));

		expect(onEdit).toHaveBeenCalledWith(apiKey);

		await user.click(screen.getByRole("button", { name: "Actions" }));
		await user.click(screen.getByRole("menuitem", { name: "Regenerate" }));

		expect(onRegenerate).toHaveBeenCalledWith(apiKey);
	});

	it("disables all mutation actions while busy", async () => {
		const user = userEvent.setup();
		renderApiDetail({ busy: true });

		expect(screen.getByRole("button", { name: "Actions" })).toBeDisabled();
		expect(screen.getByRole("button", { name: "Disable" })).toBeDisabled();
		expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
		expect(screen.getByRole("switch")).toBeEnabled();

		await user.click(screen.getByRole("switch"));
		expect(screen.getByRole("switch")).toBeChecked();
	});
});
