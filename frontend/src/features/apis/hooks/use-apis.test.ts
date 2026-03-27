import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
	createApiKey,
	createApiKeyCreateResponse,
	createApiKeyTrends,
	createApiKeyUsage7Day,
} from "@/test/mocks/factories";

const apiMocks = vi.hoisted(() => ({
	listApiKeys: vi.fn(),
	createApiKey: vi.fn(),
	updateApiKey: vi.fn(),
	deleteApiKey: vi.fn(),
	regenerateApiKey: vi.fn(),
	getApiKeyTrends: vi.fn(),
	getApiKeyUsage7Day: vi.fn(),
}));

const toastMocks = vi.hoisted(() => ({
	success: vi.fn(),
	error: vi.fn(),
}));

vi.mock("@/features/apis/api", () => apiMocks);
vi.mock("sonner", () => ({ toast: toastMocks }));

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
		return createElement(QueryClientProvider, { client: queryClient }, children);
	};
}

afterEach(() => {
	vi.clearAllMocks();
});

describe("useApiKeys", () => {
	it("loads keys and invalidates related queries after create/update/delete/regenerate", async () => {
		const queryClient = createTestQueryClient();
		const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
		const listed = [createApiKey({ id: "key_1" }), createApiKey({ id: "key_2", name: "Second" })];
		const created = createApiKeyCreateResponse({ id: "key_3", key: "sk-created" });
		const updated = createApiKey({ id: "key_1", name: "Updated key" });
		const regenerated = createApiKeyCreateResponse({ id: "key_1", key: "sk-regenerated" });

		apiMocks.listApiKeys.mockResolvedValue(listed);
		apiMocks.createApiKey.mockResolvedValue(created);
		apiMocks.updateApiKey.mockResolvedValue(updated);
		apiMocks.deleteApiKey.mockResolvedValue(undefined);
		apiMocks.regenerateApiKey.mockResolvedValue(regenerated);

		const { useApiKeys } = await import("@/features/apis/hooks/use-apis");
		const { result } = renderHook(() => useApiKeys(), {
			wrapper: createWrapper(queryClient),
		});

		await waitFor(() => expect(result.current.apiKeysQuery.isSuccess).toBe(true));
		expect(result.current.apiKeysQuery.data).toEqual(listed);

		await result.current.createMutation.mutateAsync({ name: "Created key" });
		await result.current.updateMutation.mutateAsync({
			keyId: "key_1",
			payload: { name: "Updated key" },
		});
		await result.current.deleteMutation.mutateAsync("key_2");
		await result.current.regenerateMutation.mutateAsync("key_1");

		expect(apiMocks.createApiKey).toHaveBeenCalledWith({ name: "Created key" });
		expect(apiMocks.updateApiKey).toHaveBeenCalledWith("key_1", { name: "Updated key" });
		expect(apiMocks.deleteApiKey).toHaveBeenCalledWith("key_2");
		expect(apiMocks.regenerateApiKey).toHaveBeenCalledWith("key_1");
		expect(toastMocks.success).toHaveBeenCalledWith("API key created");
		expect(toastMocks.success).toHaveBeenCalledWith("API key updated");
		expect(toastMocks.success).toHaveBeenCalledWith("API key deleted");
		expect(toastMocks.success).toHaveBeenCalledWith("API key regenerated");
		expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["api-keys", "list"] });
		expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["api-keys", "trends"] });
	});

	it("surfaces mutation failures through error toasts", async () => {
		const queryClient = createTestQueryClient();
		apiMocks.listApiKeys.mockResolvedValue([]);
		apiMocks.createApiKey.mockRejectedValue(new Error("boom create"));
		apiMocks.updateApiKey.mockRejectedValue(new Error("boom update"));
		apiMocks.deleteApiKey.mockRejectedValue(new Error("boom delete"));
		apiMocks.regenerateApiKey.mockRejectedValue(new Error("boom regenerate"));

		const { useApiKeys } = await import("@/features/apis/hooks/use-apis");
		const { result } = renderHook(() => useApiKeys(), {
			wrapper: createWrapper(queryClient),
		});

		await waitFor(() => expect(result.current.apiKeysQuery.isSuccess).toBe(true));

		await expect(result.current.createMutation.mutateAsync({ name: "Broken" })).rejects.toThrow("boom create");
		await expect(
			result.current.updateMutation.mutateAsync({ keyId: "key_1", payload: { name: "Broken" } }),
		).rejects.toThrow("boom update");
		await expect(result.current.deleteMutation.mutateAsync("key_1")).rejects.toThrow("boom delete");
		await expect(result.current.regenerateMutation.mutateAsync("key_1")).rejects.toThrow("boom regenerate");

		expect(toastMocks.error).toHaveBeenCalledWith("boom create");
		expect(toastMocks.error).toHaveBeenCalledWith("boom update");
		expect(toastMocks.error).toHaveBeenCalledWith("boom delete");
		expect(toastMocks.error).toHaveBeenCalledWith("boom regenerate");
	});
});

describe("detail queries", () => {
	it("fetches trend data only when a key is selected", async () => {
		const queryClient = createTestQueryClient();
		const response = createApiKeyTrends({ keyId: "key_1" });
		apiMocks.getApiKeyTrends.mockResolvedValue(response);

		const { useApiKeyTrends } = await import("@/features/apis/hooks/use-apis");
		const { result, rerender } = renderHook(({ keyId }) => useApiKeyTrends(keyId), {
			initialProps: { keyId: null as string | null },
			wrapper: createWrapper(queryClient),
		});

		expect(result.current.fetchStatus).toBe("idle");
		expect(apiMocks.getApiKeyTrends).not.toHaveBeenCalled();

		rerender({ keyId: "key_1" });
		await waitFor(() => expect(result.current.isSuccess).toBe(true));

		expect(result.current.data).toEqual(response);
		expect(apiMocks.getApiKeyTrends).toHaveBeenCalledWith("key_1");
	});

	it("fetches 7 day usage only when a key is selected", async () => {
		const queryClient = createTestQueryClient();
		const response = createApiKeyUsage7Day({ keyId: "key_1" });
		apiMocks.getApiKeyUsage7Day.mockResolvedValue(response);

		const { useApiKeyUsage7Day } = await import("@/features/apis/hooks/use-apis");
		const { result, rerender } = renderHook(({ keyId }) => useApiKeyUsage7Day(keyId), {
			initialProps: { keyId: null as string | null },
			wrapper: createWrapper(queryClient),
		});

		expect(result.current.fetchStatus).toBe("idle");
		expect(apiMocks.getApiKeyUsage7Day).not.toHaveBeenCalled();

		rerender({ keyId: "key_1" });
		await waitFor(() => expect(result.current.isSuccess).toBe(true));

		expect(result.current.data).toEqual(response);
		expect(apiMocks.getApiKeyUsage7Day).toHaveBeenCalledWith("key_1");
	});
});
