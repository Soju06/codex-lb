import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createApiKey } from "@/test/mocks/factories";
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

function createMutationMock(): MutationMock {
	return {
		isPending: false,
		error: null,
		mutateAsync: vi.fn(),
	};
}

function renderApisPage({
	apiKeys = [createApiKey()],
	createMutation = createMutationMock(),
	updateMutation = createMutationMock(),
	deleteMutation = createMutationMock(),
	regenerateMutation = createMutationMock(),
}: {
	apiKeys?: ReturnType<typeof createApiKey>[];
	createMutation?: MutationMock;
	updateMutation?: MutationMock;
	deleteMutation?: MutationMock;
	regenerateMutation?: MutationMock;
} = {}) {
	hookMocks.useApiKeys.mockReturnValue({
		apiKeysQuery: { data: apiKeys },
		createMutation,
		updateMutation,
		deleteMutation,
		regenerateMutation,
	});
	hookMocks.useApiKeyTrends.mockReturnValue({ data: null });
	hookMocks.useApiKeyUsage7Day.mockReturnValue({ data: null });

	return renderWithProviders(<ApisPage />);
}

afterEach(() => {
	vi.clearAllMocks();
});

describe("ApisPage", () => {
	it("keeps the create dialog open when creation fails", async () => {
		const user = userEvent.setup();
		const createMutation = createMutationMock();
		createMutation.mutateAsync.mockRejectedValue(new Error("boom create"));

		renderApisPage({ createMutation });

		await user.click(screen.getByRole("button", { name: "Create API Key" }));
		const dialog = await screen.findByRole("dialog", { name: "Create API key" });
		const nameInput = within(dialog).getByLabelText("Name");

		await user.type(nameInput, "Broken key");
		await user.click(within(dialog).getByRole("button", { name: "Create" }));

		await waitFor(() => {
			expect(createMutation.mutateAsync).toHaveBeenCalledTimes(1);
		});
		expect(screen.getByRole("dialog", { name: "Create API key" })).toBeInTheDocument();
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
		expect(screen.getByRole("dialog", { name: "Edit API key" })).toBeInTheDocument();
		expect(screen.getByLabelText("Name")).toHaveValue("Renamed key");
	});
});
