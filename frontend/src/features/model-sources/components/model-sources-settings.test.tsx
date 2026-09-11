import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test/utils";
import { ModelSourceSchema } from "@/features/model-sources/schemas";
import { ModelSourcesSettings } from "./model-sources-settings";
const state = vi.hoisted(() => ({ sources: [] as unknown[], create: vi.fn(), discover: vi.fn(), codebase: vi.fn() }));
vi.mock("@/features/model-sources/api", () => ({ discoverTraeModels: state.discover, getCodebaseModelPresets: state.codebase }));
vi.mock("@/features/model-sources/hooks/use-model-sources", () => ({
  useModelSources: () => ({
    modelSourcesQuery: { data: { sources: state.sources }, isFetching: false },
    createMutation: { mutateAsync: state.create, isPending: false },
    updateMutation: { mutateAsync: vi.fn(), isPending: false },
    deleteMutation: { mutateAsync: vi.fn(), isPending: false },
  }),
}));
beforeEach(() => { state.sources = []; state.codebase.mockReset().mockResolvedValue([{model: "codebase/kimi-k2.6", isEnabled: false}]); state.create.mockReset().mockResolvedValue({}); state.discover.mockReset().mockResolvedValue([{ model: "trae/GPT-6-Astra", isEnabled: false }]); });
describe("Company model source", () => {
  it("adds native presets with Responses support and models disabled", async () => {
    renderWithProviders(<ModelSourcesSettings />);
    await userEvent.click(screen.getByRole("button", {name: "Add Codebase / Coco (disabled)"}));
    expect(state.codebase).toHaveBeenCalledOnce();
    expect(state.create).toHaveBeenCalledWith(expect.objectContaining({kind: "codebase_llm", supportsResponses: true, supportsChatCompletions: false, models: [{model: "codebase/kimi-k2.6", isEnabled: false}]}));
  });
  it("discovers TRAE models and leaves them disabled", async () => {
    renderWithProviders(<ModelSourcesSettings />);
    await userEvent.click(screen.getByRole("button", { name: "Add TRAE (disabled)" }));
    expect(state.discover).toHaveBeenCalledOnce();
    expect(state.create).toHaveBeenCalledWith(expect.objectContaining({kind: "trae", supportsResponses: true,
      models: [{model: "trae/GPT-6-Astra", isEnabled: false}]}));
  });
  it("creates an explicit llmbox source without credentials", async () => {
    renderWithProviders(<ModelSourcesSettings />);
    await userEvent.click(screen.getByRole("button", { name: "Add LLMBox (disabled)" }));
    expect(state.create).toHaveBeenCalledOnce();
    const payload = state.create.mock.calls[0][0];
    expect(payload.kind).toBe("llmbox");
    expect(payload.baseUrl).toBe("https://llmbox.bytedance.net/v1");
    expect(payload.apiKey).toBeUndefined();
    expect(payload.models).toEqual([expect.objectContaining({model: "deepseek-v4-flash-0731", isEnabled: false})]);
  });
  it("shows unknown quota separately from partial observed usage", () => {
    state.sources = [ModelSourceSchema.parse({
      id: "src_company", name: "LLMBox", kind: "llmbox", baseUrl: "https://llmbox.bytedance.net/v1",
      isEnabled: false, healthStatus: "unknown", supportsResponses: true, supportsChatCompletions: false,
      createdAt: "2026-09-10T00:00:00Z", updatedAt: "2026-09-10T00:00:00Z", models: [],
      companyStatus: { credentialCache: "present", quotaStatus: "unknown", remaining: null, resetsAt: null,
        observedUsage: { since: "2026-09-09T00:00:00Z", requests: 3, requestsWithoutUsage: 1,
          inputTokens: 22, outputTokens: null } },
    })];
    renderWithProviders(<ModelSourcesSettings />);
    expect(screen.getByText(/Upstream quota: unknown/)).toBeInTheDocument();
    expect(screen.getByText(/cache presence alone does not prove upstream health/)).toBeInTheDocument();
    expect(screen.getByText(/3 requests.*22 input.*1 requests without complete usage/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add LLMBox (disabled)" })).toBeDisabled();
  });
});
