import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createApiKey } from "@/test/mocks/factories";

import { ApiKeyInfo } from "./api-key-info";

describe("ApiKeyInfo", () => {
	it("renders prefix, allowed models, and enforcement metadata", () => {
		render(
			<ApiKeyInfo
				apiKey={createApiKey({
					keyPrefix: "sk-special",
					allowedModels: ["gpt-5.1", "gpt-4o-mini"],
					enforcedModel: "gpt-5.1",
					enforcedReasoningEffort: "high",
				})}
			/>,
		);

		expect(screen.getByText("Key Details")).toBeInTheDocument();
		expect(screen.getByText("sk-special")).toBeInTheDocument();
		expect(screen.getByText("gpt-5.1, gpt-4o-mini")).toBeInTheDocument();
		expect(screen.getByText("Enforced Model")).toBeInTheDocument();
		expect(screen.getByText("Enforced Effort")).toBeInTheDocument();
	});

	it("falls back to all models and never expiry when the key is unrestricted", () => {
		render(
			<ApiKeyInfo
				apiKey={createApiKey({
					allowedModels: null,
					expiresAt: null,
					enforcedModel: null,
					enforcedReasoningEffort: null,
				})}
			/>,
		);

		expect(screen.getByText("All models")).toBeInTheDocument();
		expect(screen.getByText("Never")).toBeInTheDocument();
		expect(screen.queryByText("Enforced Model")).not.toBeInTheDocument();
		expect(screen.queryByText("Enforced Effort")).not.toBeInTheDocument();
	});

	it("marks expired keys clearly", () => {
		render(
			<ApiKeyInfo
				apiKey={createApiKey({
					expiresAt: "2020-01-01T00:00:00Z",
				})}
			/>,
		);

		expect(screen.getByText("Expired")).toBeInTheDocument();
	});

	it("shows no usage message when the key has no recorded traffic", () => {
		render(
			<ApiKeyInfo
				apiKey={createApiKey({
					usageSummary: {
						requestCount: 0,
						totalTokens: 0,
						cachedInputTokens: 0,
						totalCostUsd: 0,
					},
				})}
			/>,
		);

		expect(screen.getByText("No usage recorded")).toBeInTheDocument();
	});

	it("formats usage totals when summary data exists", () => {
		render(
			<ApiKeyInfo
				apiKey={createApiKey({
					usageSummary: {
						requestCount: 150,
						totalTokens: 50_000,
						cachedInputTokens: 10_000,
						totalCostUsd: 1.23,
					},
				})}
			/>,
		);

		expect(screen.getByText(/50K tok/)).toBeInTheDocument();
		expect(screen.getByText(/10K cached/)).toBeInTheDocument();
		expect(screen.getByText(/150 req/)).toBeInTheDocument();
		expect(screen.getByText(/\$1.23/)).toBeInTheDocument();
	});

	it("shows an empty limits state when no limits are configured", () => {
		render(<ApiKeyInfo apiKey={createApiKey({ limits: [] })} />);

		expect(screen.getByText("No limits configured")).toBeInTheDocument();
	});

	it("renders configured token and cost limits with model filters", () => {
		render(
			<ApiKeyInfo
				apiKey={createApiKey({
					limits: [
						{
							id: 1,
							limitType: "total_tokens",
							limitWindow: "weekly",
							maxValue: 1_000_000,
							currentValue: 750_000,
							modelFilter: "gpt-5.1",
							resetAt: "2026-01-08T00:00:00Z",
						},
						{
							id: 2,
							limitType: "cost_usd",
							limitWindow: "monthly",
							maxValue: 5_000_000,
							currentValue: 1_500_000,
							modelFilter: null,
							resetAt: "2026-02-01T00:00:00Z",
						},
					],
				})}
			/>,
		);

		expect(screen.getByText("2 configured")).toBeInTheDocument();
		expect(screen.getByText(/Total Tokens \(weekly, gpt-5.1\)/)).toBeInTheDocument();
		expect(screen.getByText(/750K \/ 1M/)).toBeInTheDocument();
		expect(screen.getByText(/Cost \(USD\) \(monthly, all\)/)).toBeInTheDocument();
		expect(screen.getByText(/\$1.50 \/ \$5.00/)).toBeInTheDocument();
	});
});
