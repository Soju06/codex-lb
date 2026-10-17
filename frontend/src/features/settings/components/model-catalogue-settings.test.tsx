import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ModelCatalogueSettings } from "@/features/settings/components/model-catalogue-settings";
import { createModelContextWindowOverrides } from "@/test/mocks/factories";

const useModelContextWindowOverridesMock = vi.fn();

vi.mock("@/features/settings/hooks/use-settings", () => ({
  useModelContextWindowOverrides: () => useModelContextWindowOverridesMock(),
}));

function mockHook(overrides = createModelContextWindowOverrides()) {
  const upsert = vi.fn().mockResolvedValue(overrides);
  const remove = vi.fn().mockResolvedValue(overrides);
  useModelContextWindowOverridesMock.mockReturnValue({
    overridesQuery: { data: overrides, error: null, isLoading: false },
    upsertMutation: { mutateAsync: upsert, isPending: false, error: null },
    deleteMutation: { mutateAsync: remove, isPending: false, error: null },
  });
  return { upsert, remove };
}

describe("ModelCatalogueSettings", () => {
  beforeEach(() => {
    useModelContextWindowOverridesMock.mockReset();
  });

  it("lists rows with per-row provenance and the clamp hint", () => {
    mockHook();
    render(<ModelCatalogueSettings />);

    expect(screen.getByText("Model catalogue")).toBeInTheDocument();
    expect(screen.getByText(/clamped to the upstream max_context_window/)).toBeInTheDocument();

    const dashboardRow = screen.getByText("gpt-5.4").closest("tr");
    expect(dashboardRow).not.toBeNull();
    expect(within(dashboardRow as HTMLElement).getByText("515,000")).toBeInTheDocument();
    expect(within(dashboardRow as HTMLElement).getByText("Dashboard")).toBeInTheDocument();
    expect(within(dashboardRow as HTMLElement).getByText("Environment: 300,000")).toBeInTheDocument();
    // A dashboard row that shadows an environment entry resets to it instead of vanishing.
    expect(within(dashboardRow as HTMLElement).getByRole("button", { name: "Reset to inherited" })).toBeInTheDocument();

    const envRow = screen.getByText("gpt-5.5").closest("tr");
    expect(envRow).not.toBeNull();
    expect(within(envRow as HTMLElement).getByText("Inherited from environment (400,000)")).toBeInTheDocument();
    // Environment-inherited rows are read-only until overridden: no remove/reset action.
    expect(within(envRow as HTMLElement).queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    expect(within(envRow as HTMLElement).queryByRole("button", { name: "Reset to inherited" })).not.toBeInTheDocument();
    expect(within(envRow as HTMLElement).getByRole("button", { name: "Override" })).toBeInTheDocument();
  });

  it("adds an override for a new slug and rejects a non-positive window client-side", async () => {
    const user = userEvent.setup();
    const { upsert } = mockHook();
    render(<ModelCatalogueSettings />);

    const addButton = screen.getByRole("button", { name: "Add override" });
    expect(addButton).toBeDisabled();

    await user.type(screen.getByLabelText("Model slug"), "custom-model");
    await user.type(screen.getByLabelText("Context window (tokens)"), "0");
    expect(addButton).toBeDisabled();

    await user.clear(screen.getByLabelText("Context window (tokens)"));
    await user.type(screen.getByLabelText("Context window (tokens)"), "32768");
    await user.click(addButton);

    expect(upsert).toHaveBeenCalledWith({ slug: "custom-model", contextWindow: 32768 });
  });

  it("overrides an environment-inherited row with the slug locked", async () => {
    const user = userEvent.setup();
    const { upsert } = mockHook();
    render(<ModelCatalogueSettings />);

    await user.click(screen.getByRole("button", { name: "Override" }));
    const slugInput = screen.getByLabelText("Model slug");
    expect(slugInput).toHaveValue("gpt-5.5");
    expect(slugInput).toBeDisabled();

    const windowInput = screen.getByLabelText("Context window (tokens)");
    expect(windowInput).toHaveValue("400000");
    await user.clear(windowInput);
    await user.type(windowInput, "600000");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(upsert).toHaveBeenCalledWith({ slug: "gpt-5.5", contextWindow: 600000 });
  });

  it("removes a dashboard row after confirmation", async () => {
    const user = userEvent.setup();
    const { remove } = mockHook(
      createModelContextWindowOverrides({
        overrides: [{ slug: "custom-model", contextWindow: 32768, source: "dashboard", envValue: null }],
      }),
    );
    render(<ModelCatalogueSettings />);

    await user.click(screen.getByRole("button", { name: "Remove" }));
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByText(/custom-model/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));

    expect(remove).toHaveBeenCalledWith("custom-model");
  });

  it("shows the empty state when nothing is overridden", () => {
    mockHook(createModelContextWindowOverrides({ overrides: [] }));
    render(<ModelCatalogueSettings />);
    expect(screen.getByText("No context window overrides")).toBeInTheDocument();
  });

  it("disables inputs when the page is read-only", () => {
    mockHook();
    render(<ModelCatalogueSettings disabled />);
    expect(screen.getByLabelText("Model slug")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Override" })).toBeDisabled();
  });
});
