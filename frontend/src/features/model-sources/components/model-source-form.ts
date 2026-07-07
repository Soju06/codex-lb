import { z } from "zod";

import type {
  ModelSource,
  ModelSourceModelInput,
} from "@/features/model-sources/schemas";

export const modelSourceFormSchema = z.object({
  name: z.string().min(1, "Name is required"),
  baseUrl: z.string().min(1, "Base URL is required"),
  apiKey: z.string(),
  models: z.string().min(1, "At least one model is required"),
});

export type ModelSourceFormValues = z.infer<typeof modelSourceFormSchema>;

// Per-model settings the dialogs apply uniformly across every model ID entered
// for the source. Pricing is USD per 1M tokens; blank means "unknown" (cost
// settles at $0 for that model).
export type ModelSourceDraft = {
  supportsChatCompletions: boolean;
  supportsResponses: boolean;
  supportsAudioTranscriptions: boolean;
  supportsStreaming: boolean;
  supportsTools: boolean;
  supportsVision: boolean;
  supportsReasoning: boolean;
  contextWindow: string;
  maxOutputTokens: string;
  inputPer1M: string;
  cachedInputPer1M: string;
  outputPer1M: string;
  audioPerMinute: string;
};

export const initialModelSourceDraft: ModelSourceDraft = {
  supportsChatCompletions: true,
  supportsResponses: false,
  supportsAudioTranscriptions: false,
  supportsStreaming: true,
  supportsTools: false,
  supportsVision: false,
  supportsReasoning: false,
  contextWindow: "",
  maxOutputTokens: "",
  inputPer1M: "",
  cachedInputPer1M: "",
  outputPer1M: "",
  audioPerMinute: "",
};

export function modelSourceDraftReducer(
  state: ModelSourceDraft,
  patch: Partial<ModelSourceDraft>,
): ModelSourceDraft {
  return { ...state, ...patch };
}

function parsePositiveInt(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const parsed = Number.parseInt(trimmed, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function parseNonNegativeFloat(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const parsed = Number.parseFloat(trimmed);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined;
}

// The backend has no first-class reasoning column; the flag lives in the
// model's raw metadata JSON, which the proxy reads to pass reasoning fields
// through and to advertise supports_reasoning in /v1/models. Merge it into
// any raw metadata the model already carries so other keys survive edits.
export function mergeReasoningFlag(
  existing: string | null | undefined,
  supportsReasoning: boolean,
): string | null {
  let metadata: Record<string, unknown> = {};
  if (existing) {
    try {
      const parsed: unknown = JSON.parse(existing);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        metadata = parsed as Record<string, unknown>;
      }
    } catch {
      metadata = {};
    }
  }
  if (supportsReasoning) {
    metadata.supports_reasoning = true;
  } else {
    delete metadata.supports_reasoning;
  }
  return Object.keys(metadata).length > 0 ? JSON.stringify(metadata) : null;
}

export function modelInputsFromForm(
  values: ModelSourceFormValues,
  draft: ModelSourceDraft,
  existingRawMetadata: Record<string, string | null> = {},
  existingEnabledByModel: Record<string, boolean> = {},
): ModelSourceModelInput[] {
  const contextWindow = parsePositiveInt(draft.contextWindow);
  const maxOutputTokens = parsePositiveInt(draft.maxOutputTokens);
  const inputPer1M = parseNonNegativeFloat(draft.inputPer1M);
  const cachedInputPer1M = parseNonNegativeFloat(draft.cachedInputPer1M);
  const outputPer1M = parseNonNegativeFloat(draft.outputPer1M);
  const audioPerMinute = parseNonNegativeFloat(draft.audioPerMinute);
  return values.models
    .split(/[\n,]/)
    .map((model) => model.trim())
    .filter(Boolean)
    .map((model) => ({
      model,
      displayName: model,
      contextWindow,
      maxOutputTokens,
      supportsStreaming: draft.supportsStreaming,
      supportsTools: draft.supportsTools,
      supportsVision: draft.supportsVision,
      inputPer1M: inputPer1M ?? null,
      cachedInputPer1M: cachedInputPer1M ?? null,
      outputPer1M: outputPer1M ?? null,
      audioPerMinute: audioPerMinute ?? null,
      rawMetadataJson: mergeReasoningFlag(existingRawMetadata[model], draft.supportsReasoning),
      isEnabled: existingEnabledByModel[model] ?? true,
    }));
}

function numberToInput(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

// Derive the shared draft from an existing source. The create UI applies one
// set of per-model settings to every model, so editing mirrors that by reading
// the first model's values as the representative settings.
function rawMetadataHasReasoning(rawMetadataJson: string | null | undefined): boolean {
  if (!rawMetadataJson) return false;
  try {
    const parsed: unknown = JSON.parse(rawMetadataJson);
    return (
      typeof parsed === "object" &&
      parsed !== null &&
      (parsed as Record<string, unknown>).supports_reasoning === true
    );
  } catch {
    return false;
  }
}

export function draftFromSource(source: ModelSource): ModelSourceDraft {
  const firstModel = source.models[0];
  return {
    supportsChatCompletions: source.supportsChatCompletions,
    supportsResponses: source.supportsResponses,
    supportsAudioTranscriptions: source.supportsAudioTranscriptions,
    supportsStreaming: firstModel?.supportsStreaming ?? true,
    supportsTools: firstModel?.supportsTools ?? false,
    supportsVision: firstModel?.supportsVision ?? false,
    supportsReasoning: rawMetadataHasReasoning(firstModel?.rawMetadataJson),
    contextWindow: numberToInput(firstModel?.contextWindow),
    maxOutputTokens: numberToInput(firstModel?.maxOutputTokens),
    inputPer1M: numberToInput(firstModel?.inputPer1M),
    cachedInputPer1M: numberToInput(firstModel?.cachedInputPer1M),
    outputPer1M: numberToInput(firstModel?.outputPer1M),
    audioPerMinute: numberToInput(firstModel?.audioPerMinute),
  };
}

export function modelIdsToInput(source: ModelSource): string {
  return source.models.map((model) => model.model).join(", ");
}

export function rawMetadataByModel(source: ModelSource): Record<string, string | null> {
  return Object.fromEntries(source.models.map((model) => [model.model, model.rawMetadataJson]));
}

export function enabledByModel(source: ModelSource): Record<string, boolean> {
  return Object.fromEntries(source.models.map((model) => [model.model, model.isEnabled]));
}
