import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type {
	ApiKeyTrendsResponse,
	ApiKeyUsage7DayResponse,
} from "@/features/apis/schemas";
import {
	createApiKeyTrends,
	createApiKeyUsage7Day,
} from "@/test/mocks/factories";

const mockGetApiKeyTrends = vi.fn();
const mockGetApiKeyUsage7Day = vi.fn();

vi.mock("@/features/apis/api", () => ({
	getApiKeyTrends: (...args: unknown[]) => mockGetApiKeyTrends(...args),
	getApiKeyUsage7Day: (...args: unknown[]) => mockGetApiKeyUsage7Day(...args),
}));

function createTestQueryClient(): QueryClient {
	return new QueryClient({
		defaultOptions: {
			queries: { retry: false, gcTime: 0 },
			mutations: { retry: false },
		},
	});
}

function createWrapper(queryClient: QueryClient) {
	return function Wrapper({ children }: PropsWithChildren) {
		return createElement(
			QueryClientProvider,
			{ client: queryClient },
			children,
		);
	};
}

describe("useApiKeyTrends", () => {
	afterEach(() => {
		vi.clearAllMocks();
	});

	beforeAll(async () => {
		await import("@/features/apis/hooks/use-apis");
	});

	it("fetches trends for a valid key", async () => {
		const queryClient = createTestQueryClient();
		const mockTrends: ApiKeyTrendsResponse = createApiKeyTrends({
			keyId: "key_1",
		});
		mockGetApiKeyTrends.mockResolvedValueOnce(mockTrends);

		const { useApiKeyTrends } = await import("@/features/apis/hooks/use-apis");
		const { result } = renderHook(() => useApiKeyTrends("key_1"), {
			wrapper: createWrapper(queryClient),
		});

		await waitFor(() => {
			expect(result.current.isSuccess).toBe(true);
		});

		expect(result.current.data).toEqual(mockTrends);
		expect(mockGetApiKeyTrends).toHaveBeenCalledWith("key_1");
	});

	it("does not fetch when keyId is null", async () => {
		const queryClient = createTestQueryClient();

		const { useApiKeyTrends } = await import("@/features/apis/hooks/use-apis");
		const { result } = renderHook(() => useApiKeyTrends(null), {
			wrapper: createWrapper(queryClient),
		});

		expect(result.current.fetchStatus).toBe("idle");
		expect(mockGetApiKeyTrends).not.toHaveBeenCalled();
	});
});

describe("useApiKeyUsage7Day", () => {
	afterEach(() => {
		vi.clearAllMocks();
	});

	beforeAll(async () => {
		await import("@/features/apis/hooks/use-apis");
	});

	it("fetches 7-day usage for a valid key", async () => {
		const queryClient = createTestQueryClient();
		const mockUsage: ApiKeyUsage7DayResponse = createApiKeyUsage7Day({
			keyId: "key_1",
		});
		mockGetApiKeyUsage7Day.mockResolvedValueOnce(mockUsage);

		const { useApiKeyUsage7Day } = await import(
			"@/features/apis/hooks/use-apis"
		);
		const { result } = renderHook(() => useApiKeyUsage7Day("key_1"), {
			wrapper: createWrapper(queryClient),
		});

		await waitFor(() => {
			expect(result.current.isSuccess).toBe(true);
		});

		expect(result.current.data).toEqual(mockUsage);
		expect(mockGetApiKeyUsage7Day).toHaveBeenCalledWith("key_1");
	});

	it("does not fetch when keyId is null", async () => {
		const queryClient = createTestQueryClient();

		const { useApiKeyUsage7Day } = await import(
			"@/features/apis/hooks/use-apis"
		);
		const { result } = renderHook(() => useApiKeyUsage7Day(null), {
			wrapper: createWrapper(queryClient),
		});

		expect(result.current.fetchStatus).toBe("idle");
		expect(mockGetApiKeyUsage7Day).not.toHaveBeenCalled();
	});
});
