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
  supportsStreaming: boolean;
  supportsTools: boolean;
  supportsVision: boolean;
  contextWindow: string;
  maxOutputTokens: string;
  inputPer1M: string;
  cachedInputPer1M: string;
  outputPer1M: string;
};

export const initialModelSourceDraft: ModelSourceDraft = {
  supportsChatCompletions: true,
  supportsResponses: false,
  supportsStreaming: true,
  supportsTools: false,
  supportsVision: false,
  contextWindow: "",
  maxOutputTokens: "",
  inputPer1M: "",
  cachedInputPer1M: "",
  outputPer1M: "",
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

export function modelInputsFromForm(
  values: ModelSourceFormValues,
  draft: ModelSourceDraft,
): ModelSourceModelInput[] {
  const contextWindow = parsePositiveInt(draft.contextWindow);
  const maxOutputTokens = parsePositiveInt(draft.maxOutputTokens);
  const inputPer1M = parseNonNegativeFloat(draft.inputPer1M);
  const cachedInputPer1M = parseNonNegativeFloat(draft.cachedInputPer1M);
  const outputPer1M = parseNonNegativeFloat(draft.outputPer1M);
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
      isEnabled: true,
    }));
}

function numberToInput(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

// Derive the shared draft from an existing source. The create UI applies one
// set of per-model settings to every model, so editing mirrors that by reading
// the first model's values as the representative settings.
export function draftFromSource(source: ModelSource): ModelSourceDraft {
  const firstModel = source.models[0];
  return {
    supportsChatCompletions: source.supportsChatCompletions,
    supportsResponses: source.supportsResponses,
    supportsStreaming: firstModel?.supportsStreaming ?? true,
    supportsTools: firstModel?.supportsTools ?? false,
    supportsVision: firstModel?.supportsVision ?? false,
    contextWindow: numberToInput(firstModel?.contextWindow),
    maxOutputTokens: numberToInput(firstModel?.maxOutputTokens),
    inputPer1M: numberToInput(firstModel?.inputPer1M),
    cachedInputPer1M: numberToInput(firstModel?.cachedInputPer1M),
    outputPer1M: numberToInput(firstModel?.outputPer1M),
  };
}

export function modelIdsToInput(source: ModelSource): string {
  return source.models.map((model) => model.model).join(", ");
}
