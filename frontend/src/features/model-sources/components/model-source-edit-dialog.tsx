import { useReducer } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Form } from "@/components/ui/form";
import { ModelSourceFormFields } from "@/features/model-sources/components/model-source-form-fields";
import {
  createModelSourceFormSchema,
  draftFromSource,
  modelIdsToInput,
  modelSourceDraftReducer,
  type ModelSourceDraft,
  type ModelSourceFormValues,
} from "@/features/model-sources/components/model-source-form";
import type {
  ModelSource,
  ModelSourceModel,
  ModelSourceUpdateRequest,
  ModelSourceModelInput,
} from "@/features/model-sources/schemas";

type ModelDraftChangeFlags = {
  contextWindow: boolean;
  maxOutputTokens: boolean;
  inputPer1M: boolean;
  cachedInputPer1M: boolean;
  outputPer1M: boolean;
  audioPerMinute: boolean;
  supportsStreaming: boolean;
  supportsTools: boolean;
  supportsVision: boolean;
  supportsReasoning: boolean;
};

const SUPPORTS_REASONING_KEY = "supports_reasoning";

function parsePositiveInt(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number.parseInt(trimmed, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function parseNonNegativeFloat(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number.parseFloat(trimmed);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function splitModelIds(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((model) => model.trim())
    .filter(Boolean);
}

function modelIdsMatch(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((model, index) => model === right[index]);
}

function getModelDraftChangeFlags(
  draft: ModelSourceDraft,
  initialDraft: ModelSourceDraft,
): ModelDraftChangeFlags {
  return {
    contextWindow: draft.contextWindow !== initialDraft.contextWindow,
    maxOutputTokens: draft.maxOutputTokens !== initialDraft.maxOutputTokens,
    inputPer1M: draft.inputPer1M !== initialDraft.inputPer1M,
    cachedInputPer1M: draft.cachedInputPer1M !== initialDraft.cachedInputPer1M,
    outputPer1M: draft.outputPer1M !== initialDraft.outputPer1M,
    audioPerMinute: draft.audioPerMinute !== initialDraft.audioPerMinute,
    supportsStreaming: draft.supportsStreaming !== initialDraft.supportsStreaming,
    supportsTools: draft.supportsTools !== initialDraft.supportsTools,
    supportsVision: draft.supportsVision !== initialDraft.supportsVision,
    supportsReasoning: draft.supportsReasoning !== initialDraft.supportsReasoning,
  };
}

function hasAnyModelDraftChange(flags: ModelDraftChangeFlags): boolean {
  return Object.values(flags).some(Boolean);
}

function mergeReasoningMetadata(
  existingMetadata: string | null | undefined,
  supportsReasoning: boolean,
): string | null {
  let metadata: Record<string, unknown> = {};
  if (existingMetadata) {
    try {
      const parsed: unknown = JSON.parse(existingMetadata);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        metadata = parsed as Record<string, unknown>;
      }
    } catch {
      metadata = {};
    }
  }
  if (supportsReasoning) {
    metadata[SUPPORTS_REASONING_KEY] = true;
  } else {
    delete metadata[SUPPORTS_REASONING_KEY];
  }
  return Object.keys(metadata).length > 0 ? JSON.stringify(metadata) : null;
}

function buildModelInputs(
  modelNames: string[],
  draft: ModelSourceDraft,
  draftChangeFlags: ModelDraftChangeFlags,
  existingModelsByName: Map<string, ModelSourceModel>,
): ModelSourceModelInput[] {
  return modelNames.map((model) => {
    const existingModel = existingModelsByName.get(model);

    return {
      model,
      displayName: existingModel?.displayName ?? model,
      contextWindow: draftChangeFlags.contextWindow
        ? parsePositiveInt(draft.contextWindow)
        : existingModel?.contextWindow ?? null,
      maxOutputTokens: draftChangeFlags.maxOutputTokens
        ? parsePositiveInt(draft.maxOutputTokens)
        : existingModel?.maxOutputTokens ?? null,
      supportsStreaming: draftChangeFlags.supportsStreaming
        ? draft.supportsStreaming
        : existingModel?.supportsStreaming ?? true,
      supportsTools: draftChangeFlags.supportsTools
        ? draft.supportsTools
        : existingModel?.supportsTools ?? false,
      supportsVision: draftChangeFlags.supportsVision
        ? draft.supportsVision
        : existingModel?.supportsVision ?? false,
      inputPer1M: draftChangeFlags.inputPer1M
        ? parseNonNegativeFloat(draft.inputPer1M)
        : existingModel?.inputPer1M ?? null,
      cachedInputPer1M: draftChangeFlags.cachedInputPer1M
        ? parseNonNegativeFloat(draft.cachedInputPer1M)
        : existingModel?.cachedInputPer1M ?? null,
      outputPer1M: draftChangeFlags.outputPer1M
        ? parseNonNegativeFloat(draft.outputPer1M)
        : existingModel?.outputPer1M ?? null,
      audioPerMinute: draftChangeFlags.audioPerMinute
        ? parseNonNegativeFloat(draft.audioPerMinute)
        : existingModel?.audioPerMinute ?? null,
      rawMetadataJson: draftChangeFlags.supportsReasoning
        ? mergeReasoningMetadata(existingModel?.rawMetadataJson, draft.supportsReasoning)
        : existingModel?.rawMetadataJson ?? null,
      isEnabled: existingModel?.isEnabled ?? true,
    };
  });
}

export type ModelSourceEditDialogProps = {
  open: boolean;
  busy: boolean;
  source: ModelSource | null;
  onOpenChange: (open: boolean) => void;
  onSubmit: (sourceId: string, payload: ModelSourceUpdateRequest) => Promise<void>;
};

type ModelSourceEditFormProps = {
  source: ModelSource;
  busy: boolean;
  onSubmit: (sourceId: string, payload: ModelSourceUpdateRequest) => Promise<void>;
  onClose: () => void;
};

function ModelSourceEditForm({ source, busy, onSubmit, onClose }: ModelSourceEditFormProps) {
  const { t } = useTranslation();
  const form = useForm<ModelSourceFormValues>({
    resolver: zodResolver(createModelSourceFormSchema(t)),
    defaultValues: {
      name: source.name,
      baseUrl: source.baseUrl,
      apiKey: "",
      models: modelIdsToInput(source),
    },
  });
  const [draft, updateDraft] = useReducer(modelSourceDraftReducer, source, draftFromSource);

  const handleSubmit = async (values: ModelSourceFormValues) => {
    const initialDraft = draftFromSource(source);
    const draftChangeFlags = getModelDraftChangeFlags(draft, initialDraft);
    const sourceModelIds = source.models.map((model) => model.model);
    const modelNames = splitModelIds(values.models);
    const modelIdsChanged = !modelIdsMatch(modelNames, sourceModelIds);

    const payload: ModelSourceUpdateRequest = {
      name: values.name,
      baseUrl: values.baseUrl,
      supportsChatCompletions: draft.supportsChatCompletions,
      supportsResponses: draft.supportsResponses,
      supportsAudioTranscriptions: draft.supportsAudioTranscriptions,
    };

    if (modelIdsChanged || hasAnyModelDraftChange(draftChangeFlags)) {
      const existingModelsByName = new Map(source.models.map((model) => [model.model, model]));
      payload.models = buildModelInputs(
        modelNames,
        draft,
        draftChangeFlags,
        existingModelsByName,
      );
    }

    // The stored key is never returned, so a blank field means "keep it";
    // only a typed value updates the credential.
    const apiKey = values.apiKey.trim();
    if (apiKey) {
      payload.apiKey = apiKey;
    }
    try {
      await onSubmit(source.id, payload);
    } catch {
      return;
    }
    onClose();
  };

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-4">
        <ModelSourceFormFields
          control={form.control}
          draft={draft}
          updateDraft={updateDraft}
	          apiKeyLabel={t("modelSources.fields.upstreamApiKey")}
	          apiKeyPlaceholder={t("modelSources.editDialog.keepCurrentKey")}
        />
        <DialogFooter>
          <Button type="submit" disabled={busy || form.formState.isSubmitting}>
	            {t("common.actions.save")}
          </Button>
        </DialogFooter>
      </form>
    </Form>
  );
}

export function ModelSourceEditDialog({
  open,
  busy,
  source,
  onOpenChange,
  onSubmit,
}: ModelSourceEditDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
	          <DialogTitle>{t("modelSources.editDialog.title")}</DialogTitle>
	          <DialogDescription>{t("modelSources.editDialog.description")}</DialogDescription>
        </DialogHeader>

        {source ? (
          <ModelSourceEditForm
            key={`${source.id}:${open ? "open" : "closed"}`}
            source={source}
            busy={busy}
            onSubmit={onSubmit}
            onClose={() => onOpenChange(false)}
          />
        ) : (
	          <p className="text-sm text-muted-foreground">{t("modelSources.editDialog.selectSource")}</p>
        )}
      </DialogContent>
    </Dialog>
  );
}
