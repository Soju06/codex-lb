import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { ADMIN_PERMISSIONS, createApiKey } from "@/test/mocks/factories";
import { renderWithProviders } from "@/test/utils";

import { ApisPage } from "./apis-page";

const hookMocks = vi.hoisted(() => ({
	useApiKeys: vi.fn(),
	useApiKeyTrends: vi.fn(),
	useApiKeyUsage7Day: vi.fn(),
}));

vi.mock("@/features/apis/hooks/use-apis", () => hookMocks);

type MutationMock = {
	isPending: boolean;
	error: Error | null;
	mutateAsync: ReturnType<typeof vi.fn>;
};

type QueryMock<T> = {
	data: T | undefined;
	error: Error | null;
	isPending: boolean;
	isFetching: boolean;
	refetch: ReturnType<typeof vi.fn>;
};

function createMutationMock(): MutationMock {
	return {
		isPending: false,
		error: null,
		mutateAsync: vi.fn(),
	};
}

function createQueryMock<T>(data: T | undefined): QueryMock<T> {
	return {
		data,
		error: null,
		isPending: false,
		isFetching: false,
		refetch: vi.fn(),
	};
}

function renderApisPage({
	apiKeys = [createApiKey()],
	apiKeysQuery = createQueryMock(apiKeys),
	trendsQuery = createQueryMock(null),
	usage7DayQuery = createQueryMock(null),
	createMutation = createMutationMock(),
	updateMutation = createMutationMock(),
	deleteMutation = createMutationMock(),
	regenerateMutation = createMutationMock(),
}: {
	apiKeys?: ReturnType<typeof createApiKey>[];
	apiKeysQuery?: QueryMock<ReturnType<typeof createApiKey>[]>;
	trendsQuery?: QueryMock<null>;
	usage7DayQuery?: QueryMock<null>;
	createMutation?: MutationMock;
	updateMutation?: MutationMock;
	deleteMutation?: MutationMock;
	regenerateMutation?: MutationMock;
} = {}) {
	hookMocks.useApiKeys.mockReturnValue({
		apiKeysQuery,
		createMutation,
		updateMutation,
		deleteMutation,
		regenerateMutation,
	});
	hookMocks.useApiKeyTrends.mockReturnValue(trendsQuery);
	hookMocks.useApiKeyUsage7Day.mockReturnValue(usage7DayQuery);

	return renderWithProviders(<ApisPage />);
}

beforeEach(() => {
  localStorage.removeItem("codex-lb-apis-view-mode");
  useAuthStore.setState({ role: "admin", permissions: ADMIN_PERMISSIONS, canWrite: true, initialized: true });
});
afterEach(() => {
	vi.clearAllMocks();
	vi.restoreAllMocks();
});

describe("ApisPage", () => {
	it("preserves filters across views and opens management for a selected List key", async () => {
		const user = userEvent.setup();
		const updateMutation = createMutationMock();
		updateMutation.mutateAsync.mockResolvedValue({});
		const key = createApiKey({
			name: "Unused client",
			lastUsedAt: null,
			usageSummary: null,
		});
		renderApisPage({ apiKeys: [key], updateMutation });
		expect(screen.getByTestId("api-key-info")).toBeInTheDocument();
		await user.click(
			screen.getByRole("combobox", { name: "Filter keys by usage" }),
		);
		await user.click(screen.getByRole("option", { name: "Key not used" }));
		await user.click(screen.getByRole("button", { name: "List view" }));
		expect(localStorage.getItem("codex-lb-apis-view-mode")).toBe("list");
		expect(screen.queryByTestId("api-key-info")).not.toBeInTheDocument();
		expect(
			screen.getByRole("combobox", { name: "Filter keys by usage" }),
		).toHaveTextContent("Key not used");
		expect(hookMocks.useApiKeyTrends).toHaveBeenLastCalledWith(null, { enabled: true });
		expect(hookMocks.useApiKeyUsage7Day).toHaveBeenLastCalledWith(null, { enabled: true });
		const row = screen.getByRole("button", {
			name: "Details for Unused client",
		});
		row.focus();
		await user.keyboard("{Enter}");
		const dialog = screen.getByRole("dialog", {
			name: "Details for Unused client",
		});
		expect(hookMocks.useApiKeyTrends).toHaveBeenLastCalledWith(key.id, { enabled: true });
		await user.click(within(dialog).getByRole("button", { name: "Disable" }));
		expect(updateMutation.mutateAsync).toHaveBeenCalledWith({
			keyId: key.id,
			payload: { isActive: false },
		});
		await user.click(within(dialog).getByRole("button", { name: "Actions" }));
		await user.click(screen.getByRole("menuitem", { name: "Edit" }));
		expect(
			await screen.findByRole("dialog", { name: "Edit API key" }),
		).toBeInTheDocument();
		await user.keyboard("{Escape}");
		await user.keyboard("{Escape}");
		await waitFor(() =>
			expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
		);
		expect(hookMocks.useApiKeyUsage7Day).toHaveBeenLastCalledWith(null, { enabled: true });
		await user.click(screen.getByRole("button", { name: "Detail view" }));
		expect(screen.getByTestId("api-key-info")).toBeInTheDocument();
		expect(
			screen.getByRole("combobox", { name: "Filter keys by usage" }),
		).toHaveTextContent("Key not used");
	});

	it("restores List and tolerates blocked browser storage", async () => {
		const user = userEvent.setup();
		localStorage.setItem("codex-lb-apis-view-mode", "list");
		const view = renderApisPage();
		expect(screen.getByTestId("api-list-overview-row")).toBeInTheDocument();
		expect(hookMocks.useApiKeyTrends).toHaveBeenLastCalledWith(null, { enabled: true });
		view.unmount();
		vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
			throw new Error("blocked");
		});
		vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
			throw new Error("blocked");
		});
		renderApisPage();
		expect(screen.getByTestId("api-key-info")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "List view" }));
		expect(screen.getByTestId("api-list-overview-row")).toBeInTheDocument();
	});
	it("keeps the create dialog open when creation fails", async () => {
		const user = userEvent.setup();
		const createMutation = createMutationMock();
		createMutation.mutateAsync.mockRejectedValue(new Error("boom create"));

		renderApisPage({ createMutation });

		await user.click(screen.getByRole("button", { name: "Create API Key" }));
		const dialog = await screen.findByRole("dialog", {
			name: "Create API key",
		});
		const nameInput = within(dialog).getByLabelText("Name");

		await user.type(nameInput, "Broken key");
		await user.click(within(dialog).getByRole("button", { name: "Create" }));

		await waitFor(() => {
			expect(createMutation.mutateAsync).toHaveBeenCalledTimes(1);
		});
		expect(
			screen.getByRole("dialog", { name: "Create API key" }),
		).toBeInTheDocument();
		expect(screen.getByLabelText("Name")).toHaveValue("Broken key");
	});

	it("keeps the edit dialog open when update fails", async () => {
		const user = userEvent.setup();
		const updateMutation = createMutationMock();
		updateMutation.mutateAsync.mockRejectedValue(new Error("boom update"));

		renderApisPage({ updateMutation });

		await user.click(screen.getByRole("button", { name: "Actions" }));
		await user.click(screen.getByRole("menuitem", { name: "Edit" }));

		const dialog = await screen.findByRole("dialog", { name: "Edit API key" });
		const nameInput = within(dialog).getByLabelText("Name");
		await user.clear(nameInput);
		await user.type(nameInput, "Renamed key");
		await user.click(within(dialog).getByRole("button", { name: "Save" }));

		await waitFor(() => {
			expect(updateMutation.mutateAsync).toHaveBeenCalledTimes(1);
		});
		expect(
			screen.getByRole("dialog", { name: "Edit API key" }),
		).toBeInTheDocument();
		expect(screen.getByLabelText("Name")).toHaveValue("Renamed key");
	});

	it("shows a retry state when the initial API key query fails", async () => {
		const user = userEvent.setup();
		const apiKeysQuery =
			createQueryMock<ReturnType<typeof createApiKey>[]>(undefined);
		apiKeysQuery.error = new Error("boom list");

		renderApisPage({ apiKeys: [], apiKeysQuery });

		expect(screen.getByText("boom list")).toBeInTheDocument();
		const retryButton = screen.getByRole("button", { name: "Retry" });
		await user.click(retryButton);

		expect(apiKeysQuery.refetch).toHaveBeenCalledTimes(1);
		expect(screen.queryByText("Create API Key")).not.toBeInTheDocument();
	});

	it("shows an administrator-only notice and keeps API key queries idle for read-only guests", () => {
		useAuthStore.setState({ role: "guest", permissions: ["read"], canWrite: false, initialized: true });

		renderApisPage();

		expect(screen.getByRole("heading", { name: "APIs" })).toBeInTheDocument();
		expect(screen.getByRole("status")).toHaveTextContent("API keys are managed by administrators");
		expect(screen.getByRole("status")).toHaveTextContent(
			"Sign in as an administrator to view and manage API keys.",
		);
		expect(hookMocks.useApiKeys).toHaveBeenCalledWith({ enabled: false });
		// Even with a (stale) cached key list, per-key queries stay idle.
		expect(hookMocks.useApiKeyTrends).toHaveBeenCalledWith("key_1", { enabled: false });
		expect(hookMocks.useApiKeyUsage7Day).toHaveBeenCalledWith("key_1", { enabled: false });
		// No key list, no create/edit/delete controls, no error card.
		expect(screen.queryByText("Overview")).not.toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Create API Key" })).not.toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
	});

	it.each(["detail", "list"])("lists keys without write actions for api_keys:read in %s view", async (mode) => {
    localStorage.setItem("codex-lb-apis-view-mode", mode);
		useAuthStore.setState({
			role: "admin",
			permissions: ["read", "api_keys:read:all", "dashboard:read:all"],
			canWrite: false,
			initialized: true,
		});

		renderApisPage();

		expect(hookMocks.useApiKeys).toHaveBeenCalledWith({ enabled: true });
		expect(screen.getByText("Overview")).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Create API Key" })).not.toBeInTheDocument();
    if (mode === "list") {
      await userEvent.setup().click(screen.getAllByTestId("api-list-overview-row")[0]);
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    }
		expect(screen.queryByRole("button", { name: "Actions" })).not.toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
	});

	it("enables the API key queries for writers", () => {
		renderApisPage();

		expect(hookMocks.useApiKeys).toHaveBeenCalledWith({ enabled: true });
		expect(hookMocks.useApiKeyTrends).toHaveBeenCalledWith("key_1", { enabled: true });
		expect(screen.getByRole("button", { name: "Create API Key" })).toBeInTheDocument();
	});

	it("labels the legacy limit bar as API Limit", () => {
		renderApisPage({
			apiKeys: [
				createApiKey({
					pooledRemainingPercentPrimary: 67.5,
					pooledRemainingPercentSecondary: 85,
					pooledCapacityCreditsPrimary: 225,
				}),
			],
		});

		expect(screen.getByText("Pooled 5h")).toBeInTheDocument();
		expect(screen.getByText("Pooled Weekly")).toBeInTheDocument();
		expect(screen.getByText("API Limit")).toBeInTheDocument();
	});

	it("renders the overview section for the full API key list", () => {
		renderApisPage({
			apiKeys: [
				createApiKey({
					usageSummary: {
						requestCount: 300,
						totalTokens: 80_000,
						cachedInputTokens: 12_000,
						totalCostUsd: 2.5,
					},
				}),
				createApiKey({
					id: "key_2",
					name: "Secondary key",
					keyPrefix: "sk-secondary",
					usageSummary: {
						requestCount: 120,
						totalTokens: 20_000,
						cachedInputTokens: 2_000,
						totalCostUsd: 1.0,
					},
				}),
			],
		});

		expect(screen.getByText("Overview")).toBeInTheDocument();
		expect(screen.getByText("Lifetime Cost by API Key")).toBeInTheDocument();
		expect(screen.getByText("Lifetime Tokens by API Key")).toBeInTheDocument();
		expect(
			within(screen.getByTestId("api-keys-overview-cost-panel")).getByText(
				"Secondary key",
			),
		).toBeInTheDocument();
	});
});
