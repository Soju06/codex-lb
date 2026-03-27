import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createApiKey } from "@/test/mocks/factories";

import { ApiKeyInfo } from "./api-key-info";

describe("ApiKeyInfo", () => {
	it("renders key details with prefix and models", () => {
		const apiKey = createApiKey({
			name: "My Key",
			keyPrefix: "sk-abc123",
			allowedModels: ["gpt-4o", "gpt-5.1"],
		});

		render(<ApiKeyInfo apiKey={apiKey} />);

		expect(screen.getByText("Key Details")).toBeInTheDocument();
		expect(screen.getByText("sk-abc123")).toBeInTheDocument();
		expect(screen.getByText("gpt-4o, gpt-5.1")).toBeInTheDocument();
	});

	it("renders All models when allowedModels is null", () => {
		const apiKey = createApiKey({ allowedModels: null });

		render(<ApiKeyInfo apiKey={apiKey} />);

		expect(screen.getByText("All models")).toBeInTheDocument();
	});

	it("renders enforced model and effort when set", () => {
		const apiKey = createApiKey({
			allowedModels: ["gpt-4o"],
			enforcedModel: "gpt-5.1",
			enforcedReasoningEffort: "high",
		});

		render(<ApiKeyInfo apiKey={apiKey} />);

		expect(screen.getByText("Enforced Model")).toBeInTheDocument();
		expect(screen.getByText("gpt-5.1")).toBeInTheDocument();
		expect(screen.getByText("high")).toBeInTheDocument();
	});

	it("hides enforced model and effort when null", () => {
		const apiKey = createApiKey({
			enforcedModel: null,
			enforcedReasoningEffort: null,
		});

		render(<ApiKeyInfo apiKey={apiKey} />);

		expect(screen.queryByText("Enforced Model")).not.toBeInTheDocument();
		expect(screen.queryByText("Enforced Effort")).not.toBeInTheDocument();
	});

	it("renders expiry as Never when null", () => {
		const apiKey = createApiKey({ expiresAt: null });

		render(<ApiKeyInfo apiKey={apiKey} />);

		expect(screen.getByText("Never")).toBeInTheDocument();
	});

	it("renders No usage recorded when no usage", () => {
		const apiKey = createApiKey({
			usageSummary: {
				requestCount: 0,
				totalTokens: 0,
				cachedInputTokens: 0,
				totalCostUsd: 0,
			},
		});

		render(<ApiKeyInfo apiKey={apiKey} />);

		expect(screen.getByText("No usage recorded")).toBeInTheDocument();
	});

	it("renders usage data inline", () => {
		const apiKey = createApiKey({
			usageSummary: {
				requestCount: 150,
				totalTokens: 50_000,
				cachedInputTokens: 10_000,
				totalCostUsd: 1.23,
			},
		});

		render(<ApiKeyInfo apiKey={apiKey} />);

		expect(screen.getByText(/50K tok/)).toBeInTheDocument();
		expect(screen.getByText(/10K cached/)).toBeInTheDocument();
		expect(screen.getByText(/150 req/)).toBeInTheDocument();
		expect(screen.getByText(/\$1.23/)).toBeInTheDocument();
	});

	it("renders No limits configured when no limits", () => {
		const apiKey = createApiKey({ limits: [] });

		render(<ApiKeyInfo apiKey={apiKey} />);

		expect(screen.getByText("No limits configured")).toBeInTheDocument();
	});

	it("renders limit count when limits exist", () => {
		const apiKey = createApiKey({
			limits: [
				{
					id: 1,
					limitType: "total_tokens",
					limitWindow: "weekly",
					maxValue: 1_000_000,
					currentValue: 250_000,
					modelFilter: null,
					resetAt: new Date().toISOString(),
				},
				{
					id: 2,
					limitType: "cost_usd",
					limitWindow: "monthly",
					maxValue: 5_000_000,
					currentValue: 1_000_000,
					modelFilter: null,
					resetAt: new Date().toISOString(),
				},
			],
		});

		render(<ApiKeyInfo apiKey={apiKey} />);

		expect(screen.getByText("2 configured")).toBeInTheDocument();
	});

	it("renders individual limit details with progress bars", () => {
		const apiKey = createApiKey({
			limits: [
				{
					id: 1,
					limitType: "total_tokens",
					limitWindow: "weekly",
					maxValue: 1_000_000,
					currentValue: 750_000,
					modelFilter: "gpt-5.1",
					resetAt: new Date().toISOString(),
				},
			],
		});

		render(<ApiKeyInfo apiKey={apiKey} />);

		expect(
			screen.getByText(/Total Tokens \(weekly, gpt-5.1\)/),
		).toBeInTheDocument();
		expect(screen.getByText(/750K \/ 1M/)).toBeInTheDocument();
	});
});
