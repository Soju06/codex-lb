import { useReducer } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";

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
  draftFromSource,
  modelIdsToInput,
  modelInputsFromForm,
  modelSourceDraftReducer,
  modelSourceFormSchema,
  type ModelSourceFormValues,
} from "@/features/model-sources/components/model-source-form";
import type {
  ModelSource,
  ModelSourceUpdateRequest,
} from "@/features/model-sources/schemas";

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
  const form = useForm<ModelSourceFormValues>({
    resolver: zodResolver(modelSourceFormSchema),
    defaultValues: {
      name: source.name,
      baseUrl: source.baseUrl,
      apiKey: "",
      models: modelIdsToInput(source),
    },
  });
  const [draft, updateDraft] = useReducer(modelSourceDraftReducer, source, draftFromSource);

  const handleSubmit = async (values: ModelSourceFormValues) => {
    const payload: ModelSourceUpdateRequest = {
      name: values.name,
      baseUrl: values.baseUrl,
      supportsChatCompletions: draft.supportsChatCompletions,
      supportsResponses: draft.supportsResponses,
      models: modelInputsFromForm(values, draft),
    };
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
          apiKeyLabel="Upstream API key"
          apiKeyPlaceholder="Leave blank to keep current key"
        />
        <DialogFooter>
          <Button type="submit" disabled={busy || form.formState.isSubmitting}>
            Save
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
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit model source</DialogTitle>
          <DialogDescription>Update the endpoint, models, pricing, and capabilities.</DialogDescription>
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
          <p className="text-sm text-muted-foreground">Select a model source to edit.</p>
        )}
      </DialogContent>
    </Dialog>
  );
}
