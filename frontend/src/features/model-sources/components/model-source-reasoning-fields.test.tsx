import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ModelSource } from "@/features/model-sources/schemas";
import { renderWithProviders } from "@/test/utils";

import { ModelSourceEditDialog } from "./model-source-edit-dialog";

function reasoningSource(): ModelSource {
  return {
    id: "src_reasoning",
    name: "reasoning-source",
    kind: "openai_compatible",
    baseUrl: "https://api.example.com/v1",
    isEnabled: true,
    healthStatus: "unknown",
    supportsChatCompletions: false,
    supportsResponses: true,
    supportsAudioTranscriptions: false,
    timeoutSeconds: null,
    maxConcurrency: null,
    createdAt: "2026-08-08T00:00:00Z",
    updatedAt: "2026-08-08T00:00:00Z",
    models: [
      {
        id: 1,
        sourceId: "src_reasoning",
        model: "reasoning-model",
        displayName: "Reasoning Model",
        contextWindow: 32768,
        maxOutputTokens: 4096,
        supportsStreaming: true,
        supportsTools: true,
        supportsVision: false,
        inputPer1M: null,
        cachedInputPer1M: null,
        outputPer1M: null,
        audioPerMinute: null,
        rawMetadataJson: JSON.stringify({
          supports_reasoning: true,
          supported_reasoning_levels: ["minimal", "low", "medium", "high", "xhigh"],
          default_reasoning_level: "high",
        }),
        isEnabled: true,
        createdAt: "2026-08-08T00:00:00Z",
        updatedAt: "2026-08-08T00:00:00Z",
      },
    ],
  };
}

describe("ModelSource reasoning controls", () => {
  it("prefills and submits supported reasoning levels", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <ModelSourceEditDialog
        open
        busy={false}
        source={reasoningSource()}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByRole("checkbox", { name: "Reasoning" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "minimal" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "xhigh" })).toBeChecked();

    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));

    const rawMetadata = JSON.parse(onSubmit.mock.calls[0][1].models[0].rawMetadataJson);
    expect(rawMetadata).toMatchObject({
      supports_reasoning: true,
      supported_reasoning_levels: ["minimal", "low", "medium", "high", "xhigh"],
      default_reasoning_level: "high",
    });
  });
});
