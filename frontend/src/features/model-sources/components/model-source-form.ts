import { z } from "zod";
import type { TFunction } from "i18next";

import type {
  ModelSource,
  ModelSourceModelInput,
} from "@/features/model-sources/schemas";

export function createModelSourceFormSchema(t: TFunction) {
  return z.object({
    name: z.string().min(1, t("modelSources.validation.nameRequired")),
    baseUrl: z.string().min(1, t("modelSources.validation.baseUrlRequired")),
    apiKey: z.string(),
    models: z.string().min(1, t("modelSources.validation.modelsRequired")),
  });
}

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
  supportsEmbeddings: boolean;
  supportsStreaming: boolean;
  supportsTools: boolean;
  supportsVision: boolean;
  supportsReasoning: boolean;
  reasoningEffortsInput: string;
  reasoningEfforts: string[];
  defaultReasoningEffort: string;
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
  supportsEmbeddings: false,
  supportsStreaming: true,
  supportsTools: false,
  supportsVision: false,
  supportsReasoning: false,
  reasoningEffortsInput: "",
  reasoningEfforts: [],
  defaultReasoningEffort: "",
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
const DEFAULT_REASONING_EFFORTS = ["low", "medium", "high"];

function normalizeReasoningEffort(value: string): string {
  return value.trim();
}

function dedupeReasoningEfforts(values: Iterable<string>): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const normalized = normalizeReasoningEffort(value);
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

export function parseReasoningEffortsInput(value: string): string[] {
  return dedupeReasoningEfforts(value.split(/[\n,]/));
}

function normalizeDefaultReasoningEffort(
  reasoningEfforts: string[],
  defaultReasoningEffort: string,
): string {
  const normalizedDefault = normalizeReasoningEffort(defaultReasoningEffort);
  if (normalizedDefault && reasoningEfforts.includes(normalizedDefault)) {
    return normalizedDefault;
  }
  return reasoningEfforts[0] ?? "";
}

export function mergeReasoningMetadata(
  existing: string | null | undefined,
  supportsReasoning: boolean,
  reasoningEfforts: string[] = [],
  defaultReasoningEffort = "",
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
    if (reasoningEfforts.length > 0) {
      metadata.supported_reasoning_levels = reasoningEfforts;
      metadata.default_reasoning_level = normalizeDefaultReasoningEffort(
        reasoningEfforts,
        defaultReasoningEffort,
      );
    } else {
      delete metadata.supported_reasoning_levels;
      delete metadata.default_reasoning_level;
    }
  } else {
    delete metadata.supports_reasoning;
    delete metadata.supported_reasoning_levels;
    delete metadata.default_reasoning_level;
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
      rawMetadataJson: mergeReasoningMetadata(
        existingRawMetadata[model],
        draft.supportsReasoning,
        draft.reasoningEfforts,
        draft.defaultReasoningEffort,
      ),
      isEnabled: existingEnabledByModel[model] ?? true,
    }));
}

function numberToInput(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

// Derive the shared draft from an existing source. The create UI applies one
// set of per-model settings to every model, so editing mirrors that by reading
// the first model's values as the representative settings.
function parseReasoningMetadata(rawMetadataJson: string | null | undefined): {
  supportsReasoning: boolean;
  reasoningEffortsInput: string;
  reasoningEfforts: string[];
  defaultReasoningEffort: string;
} {
  const fallback = {
    supportsReasoning: false,
    reasoningEffortsInput: "",
    reasoningEfforts: [],
    defaultReasoningEffort: "",
  };
  if (!rawMetadataJson) return fallback;
  try {
    const parsed: unknown = JSON.parse(rawMetadataJson);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return fallback;
    }
    const metadata = parsed as Record<string, unknown>;
    const supportsReasoning = metadata.supports_reasoning === true;
    const declaredLevels = Array.isArray(metadata.supported_reasoning_levels)
      ? dedupeReasoningEfforts(
          metadata.supported_reasoning_levels.flatMap((value): string[] => {
            if (typeof value === "string") return [value];
            if (typeof value !== "object" || value === null || Array.isArray(value)) return [];
            const effort = (value as Record<string, unknown>).effort;
            return typeof effort === "string" ? [effort] : [];
          }),
        )
      : [];
    const reasoningEfforts =
      supportsReasoning && declaredLevels.length === 0 ? DEFAULT_REASONING_EFFORTS : declaredLevels;

    return {
      supportsReasoning,
      reasoningEffortsInput: reasoningEfforts.join(", "),
      reasoningEfforts,
      defaultReasoningEffort: normalizeDefaultReasoningEffort(
        reasoningEfforts,
        typeof metadata.default_reasoning_level === "string"
          ? metadata.default_reasoning_level
          : "",
      ),
    };
  } catch {
    return fallback;
  }
}

export function draftFromSource(source: ModelSource): ModelSourceDraft {
  const firstModel = source.models[0];
  const reasoningMetadata = parseReasoningMetadata(firstModel?.rawMetadataJson);
  return {
    supportsChatCompletions: source.supportsChatCompletions,
    supportsResponses: source.supportsResponses,
    supportsAudioTranscriptions: source.supportsAudioTranscriptions,
    supportsEmbeddings: source.supportsEmbeddings,
    supportsStreaming: firstModel?.supportsStreaming ?? true,
    supportsTools: firstModel?.supportsTools ?? false,
    supportsVision: firstModel?.supportsVision ?? false,
    supportsReasoning: reasoningMetadata.supportsReasoning,
    reasoningEffortsInput: reasoningMetadata.reasoningEffortsInput,
    reasoningEfforts: reasoningMetadata.reasoningEfforts,
    defaultReasoningEffort: reasoningMetadata.defaultReasoningEffort,
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
