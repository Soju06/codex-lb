import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";
import type { ModelSource } from "@/features/model-sources/schemas";

import { ModelSourceEditDialog } from "./model-source-edit-dialog";

function createModelSource(overrides: Partial<ModelSource> = {}): ModelSource {
  return {
    id: "src_1",
    name: "vllm-local",
    kind: "openai_compatible",
    baseUrl: "http://127.0.0.1:8000/v1",
    isEnabled: true,
    healthStatus: "unknown",
    supportsChatCompletions: true,
    supportsResponses: false,
    supportsAudioTranscriptions: false,
    timeoutSeconds: null,
    maxConcurrency: null,
    createdAt: "2026-07-03T00:00:00Z",
    updatedAt: "2026-07-03T00:00:00Z",
    models: [
      {
        id: 1,
        sourceId: "src_1",
        model: "Qwen/Qwen3.6-27B-FP8",
        displayName: "Qwen/Qwen3.6-27B-FP8",
        contextWindow: 32768,
        maxOutputTokens: 4096,
        supportsStreaming: true,
        supportsTools: true,
        supportsVision: false,
        inputPer1M: 0.5,
        cachedInputPer1M: null,
        outputPer1M: 1.5,
        audioPerMinute: null,
        rawMetadataJson: null,
        isEnabled: true,
        createdAt: "2026-07-03T00:00:00Z",
        updatedAt: "2026-07-03T00:00:00Z",
      },
    ],
    ...overrides,
  };
}

describe("ModelSourceEditDialog", () => {
  it("prefills existing fields including pricing and models", () => {
    renderWithProviders(
      <ModelSourceEditDialog
        open
        busy={false}
        source={createModelSource()}
        onOpenChange={vi.fn()}
        onSubmit={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByLabelText("Name")).toHaveValue("vllm-local");
    expect(screen.getByLabelText("Base URL")).toHaveValue("http://127.0.0.1:8000/v1");
    expect(screen.getByDisplayValue("Qwen/Qwen3.6-27B-FP8")).toBeInTheDocument();
    expect(screen.getByDisplayValue("0.5")).toBeInTheDocument();
    expect(screen.getByDisplayValue("1.5")).toBeInTheDocument();
  });

  it("submits edited pricing and omits blank api key", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <ModelSourceEditDialog
        open
        busy={false}
        source={createModelSource()}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    const outputPrice = screen.getByDisplayValue("1.5");
    await user.clear(outputPrice);
    await user.type(outputPrice, "2.25");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const [sourceId, payload] = onSubmit.mock.calls[0];
    expect(sourceId).toBe("src_1");
    expect("apiKey" in payload).toBe(false);
    expect(payload.models).toHaveLength(1);
    expect(payload.models[0]).toMatchObject({
      model: "Qwen/Qwen3.6-27B-FP8",
      inputPer1M: 0.5,
      outputPer1M: 2.25,
    });
    expect(payload.supportsAudioTranscriptions).toBe(false);
  });

  it("preserves disabled model rows during edits", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const source = createModelSource({
      models: [
        {
          ...createModelSource().models[0],
          model: "enabled-model",
          displayName: "enabled-model",
          isEnabled: true,
          contextWindow: 2048,
          supportsVision: true,
        },
        {
          ...createModelSource().models[0],
          id: 2,
          model: "disabled-model",
          displayName: "disabled-model",
          isEnabled: false,
          contextWindow: 4096,
          supportsVision: false,
        },
      ],
    });

    renderWithProviders(
      <ModelSourceEditDialog
        open
        busy={false}
        source={source}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    const outputPrice = screen.getByDisplayValue("1.5");
    await user.clear(outputPrice);
    await user.type(outputPrice, "2.0");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    expect(onSubmit.mock.calls[0][1].models).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          model: "enabled-model",
          isEnabled: true,
          contextWindow: 2048,
          outputPer1M: 2,
          supportsVision: true,
        }),
        expect.objectContaining({
          model: "disabled-model",
          isEnabled: false,
          contextWindow: 4096,
          outputPer1M: 2,
          supportsVision: false,
        }),
      ]),
    );
  });

  it("omits model rows when only source-level fields changed", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const source = createModelSource({
      models: [
        {
          ...createModelSource().models[0],
          model: "m-one",
          displayName: "m-one",
          contextWindow: 2048,
          outputPer1M: 1.0,
        },
        {
          ...createModelSource().models[0],
          id: 2,
          model: "m-two",
          displayName: "m-two",
          contextWindow: 4096,
          outputPer1M: 2.0,
        },
      ],
    });

    renderWithProviders(
      <ModelSourceEditDialog
        open
        busy={false}
        source={source}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "vllm-local-updated");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const payload = onSubmit.mock.calls[0][1];
    expect(payload.name).toBe("vllm-local-updated");
    expect(payload.models).toBeUndefined();
  });

  it("prefills and submits the audio per-minute rate", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const source = createModelSource();
    source.models[0].audioPerMinute = 0.3;

    renderWithProviders(
      <ModelSourceEditDialog
        open
        busy={false}
        source={source}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    const audioPrice = screen.getByDisplayValue("0.3");
    await user.clear(audioPrice);
    await user.type(audioPrice, "0.45");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    expect(onSubmit.mock.calls[0][1].models[0].audioPerMinute).toBe(0.45);
  });

  it("toggles reasoning support via raw metadata while keeping other keys", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const source = createModelSource();
    source.models[0].rawMetadataJson = '{"custom_key": "kept"}';

    renderWithProviders(
      <ModelSourceEditDialog
        open
        busy={false}
        source={source}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByRole("checkbox", { name: "Reasoning" })).not.toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "Reasoning" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const rawMetadata = JSON.parse(onSubmit.mock.calls[0][1].models[0].rawMetadataJson);
    expect(rawMetadata).toEqual({ custom_key: "kept", supports_reasoning: true });
  });

  it("prefills the reasoning toggle from raw metadata", () => {
    const source = createModelSource();
    source.models[0].rawMetadataJson = '{"supports_reasoning": true}';

    renderWithProviders(
      <ModelSourceEditDialog
        open
        busy={false}
        source={source}
        onOpenChange={vi.fn()}
        onSubmit={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByRole("checkbox", { name: "Reasoning" })).toBeChecked();
  });

  it("sends the api key only when the field is filled", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <ModelSourceEditDialog
        open
        busy={false}
        source={createModelSource()}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await user.type(screen.getByLabelText("Upstream API key"), "sk-new-token");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    expect(onSubmit.mock.calls[0][1].apiKey).toBe("sk-new-token");
  });
});
