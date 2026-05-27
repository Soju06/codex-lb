import { act, fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { createApiKeyUsage7Day } from "@/test/mocks/factories";
import { renderWithProviders } from "@/test/utils";
import { usePrivacyStore } from "@/hooks/use-privacy";

import { AccountCostDonut } from "./account-cost-donut";

describe("AccountCostDonut", () => {
	it("highlights the matching legend row when a legend item is hovered", () => {
		const usage = createApiKeyUsage7Day({
			totalCostUsd: 0.75,
			accountCosts: [
				{ accountId: "acc-1", email: "a@example.com", costUsd: 0.45, isDeleted: false },
				{ accountId: "acc-2", email: "b@example.com", costUsd: 0.3, isDeleted: false },
			],
		});

		renderWithProviders(
			<AccountCostDonut accountCosts={usage.accountCosts} totalCostUsd={usage.totalCostUsd} />,
		);

		const legendRow = screen.getByTestId("account-cost-legend-0");
		fireEvent.mouseEnter(legendRow);
		expect(legendRow).toHaveAttribute("data-active", "true");

		fireEvent.mouseLeave(legendRow);
		expect(legendRow).toHaveAttribute("data-active", "false");
	});

	it("highlights the matching legend row when a pie slice is hovered", () => {
		const usage = createApiKeyUsage7Day({
			totalCostUsd: 0.75,
			accountCosts: [
				{ accountId: "acc-1", email: "a@example.com", costUsd: 0.45, isDeleted: false },
				{ accountId: "acc-2", email: "b@example.com", costUsd: 0.3, isDeleted: false },
			],
		});

		renderWithProviders(
			<AccountCostDonut accountCosts={usage.accountCosts} totalCostUsd={usage.totalCostUsd} />,
		);

		const slices = document.querySelectorAll(".recharts-pie-sector");
		fireEvent.mouseEnter(slices[0]!);

		expect(screen.getByTestId("account-cost-legend-0")).toHaveAttribute("data-active", "true");
	});

	it("limits the legend viewport to five visible rows before scrolling", () => {
		const usage = createApiKeyUsage7Day({
			totalCostUsd: 2.8,
			accountCosts: Array.from({ length: 6 }, (_, index) => ({
				accountId: `acc-${index}`,
				email: `user${index}@example.com`,
				costUsd: 0.4 + index * 0.05,
				isDeleted: false,
			})),
		});

		renderWithProviders(
			<AccountCostDonut accountCosts={usage.accountCosts} totalCostUsd={usage.totalCostUsd} />,
		);

		expect(screen.getByTestId("account-cost-legend-list")).toHaveStyle({
			maxHeight: "calc(5 * 1.75rem + 4 * 0rem)",
		});
		expect(screen.getByTestId("account-cost-legend-5")).toBeInTheDocument();
	});

	it("renders the legend below the donut and omits the header total summary", () => {
		const usage = createApiKeyUsage7Day({
			totalCostUsd: 0.75,
			accountCosts: [
				{ accountId: "acc-1", email: "a@example.com", costUsd: 0.45, isDeleted: false },
				{ accountId: "acc-2", email: "b@example.com", costUsd: 0.3, isDeleted: false },
			],
		});

		renderWithProviders(
			<AccountCostDonut accountCosts={usage.accountCosts} totalCostUsd={usage.totalCostUsd} />,
		);

		expect(screen.queryByTestId("account-cost-total")).not.toBeInTheDocument();
		expect(screen.getByTestId("account-cost-legend-list")).toHaveClass("w-full");
	});

	it("blurs active account labels but not deleted account labels in privacy mode", () => {
		act(() => usePrivacyStore.setState({ blurred: true }));
		const usage = createApiKeyUsage7Day({
			totalCostUsd: 0.75,
			accountCosts: [
				{ accountId: "acc-1", email: "a@example.com", costUsd: 0.45, isDeleted: false },
				{ accountId: null, email: null, costUsd: 0.3, isDeleted: true },
			],
		});

		renderWithProviders(
			<AccountCostDonut accountCosts={usage.accountCosts} totalCostUsd={usage.totalCostUsd} />,
		);

		expect(screen.getByText("a@example.com")).toHaveClass("privacy-blur");
		expect(screen.getByText("Deleted Account")).not.toHaveClass("privacy-blur");
		act(() => usePrivacyStore.setState({ blurred: false }));
	});

	it("scrolls the hovered pie item into view in the legend list", async () => {
		const scrollIntoView = vi.fn();
		Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
			configurable: true,
			value: scrollIntoView,
		});

		const usage = createApiKeyUsage7Day({
			totalCostUsd: 5.6,
			accountCosts: Array.from({ length: 6 }, (_, index) => ({
				accountId: `acc-${index}`,
				email: `user${index}@example.com`,
				costUsd: 1 - index * 0.1,
				isDeleted: false,
			})),
		});

		renderWithProviders(
			<AccountCostDonut accountCosts={usage.accountCosts} totalCostUsd={usage.totalCostUsd} />,
		);

		const slices = document.querySelectorAll(".recharts-pie-sector");
		fireEvent.mouseEnter(slices[5]!);

		expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest", inline: "nearest" });
	});
});
