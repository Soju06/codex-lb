import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ModelSource } from "@/features/model-sources/schemas";
import { renderWithProviders } from "@/test/utils";

import { ModelSourceEditDialog } from "./model-source-edit-dialog";

function fallbackSource(): ModelSource {
  return {
    id: "src_fallback",
    name: "fallback-source",
    kind: "openai_compatible",
    baseUrl: "https://api.example.com/v1",
    isEnabled: true,
    healthStatus: "unknown",
    supportsChatCompletions: false,
    supportsResponses: true,
    isSubscriptionFallback: true,
    fallbackModel: "external-coder",
    supportsAudioTranscriptions: false,
    timeoutSeconds: null,
    maxConcurrency: null,
    createdAt: "2026-08-08T00:00:00Z",
    updatedAt: "2026-08-08T00:00:00Z",
    models: [
      {
        id: 1,
        sourceId: "src_fallback",
        model: "external-coder",
        displayName: "External Coder",
        contextWindow: 32768,
        maxOutputTokens: 4096,
        supportsStreaming: true,
        supportsTools: true,
        supportsVision: false,
        inputPer1M: null,
        cachedInputPer1M: null,
        outputPer1M: null,
        audioPerMinute: null,
        rawMetadataJson: null,
        isEnabled: true,
        createdAt: "2026-08-08T00:00:00Z",
        updatedAt: "2026-08-08T00:00:00Z",
      },
    ],
  };
}

describe("ModelSource subscription fallback controls", () => {
  it("prefills the fallback designation and model override", () => {
    renderWithProviders(
      <ModelSourceEditDialog
        open
        busy={false}
        source={fallbackSource()}
        onOpenChange={vi.fn()}
        onSubmit={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByRole("checkbox", { name: "Use as subscription fallback" })).toBeChecked();
    expect(screen.getByPlaceholderText("Leave blank to preserve the requested model")).toHaveValue(
      "external-coder",
    );
  });

  it("submits fallback disablement and clears the model override", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <ModelSourceEditDialog
        open
        busy={false}
        source={fallbackSource()}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await user.click(screen.getByRole("checkbox", { name: "Use as subscription fallback" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][1]).toMatchObject({
      supportsResponses: true,
      isSubscriptionFallback: false,
      fallbackModel: "external-coder",
    });
  });
});
