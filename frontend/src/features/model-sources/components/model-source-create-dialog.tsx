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
  initialModelSourceDraft,
  modelInputsFromForm,
  modelSourceDraftReducer,
  modelSourceFormSchema,
  type ModelSourceFormValues,
} from "@/features/model-sources/components/model-source-form";
import type { ModelSourceCreateRequest } from "@/features/model-sources/schemas";

export type ModelSourceCreateDialogProps = {
  open: boolean;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: ModelSourceCreateRequest) => Promise<void>;
};

export function ModelSourceCreateDialog({
  open,
  busy,
  onOpenChange,
  onSubmit,
}: ModelSourceCreateDialogProps) {
  const form = useForm<ModelSourceFormValues>({
    resolver: zodResolver(modelSourceFormSchema),
    defaultValues: {
      name: "",
      baseUrl: "",
      apiKey: "",
      models: "",
    },
  });
  const [draft, updateDraft] = useReducer(modelSourceDraftReducer, initialModelSourceDraft);

  const handleSubmit = async (values: ModelSourceFormValues) => {
    const payload: ModelSourceCreateRequest = {
      name: values.name,
      baseUrl: values.baseUrl,
      apiKey: values.apiKey.trim() ? values.apiKey.trim() : undefined,
      supportsChatCompletions: draft.supportsChatCompletions,
      supportsResponses: draft.supportsResponses,
      supportsAudioTranscriptions: draft.supportsAudioTranscriptions,
      models: modelInputsFromForm(values, draft),
    };
    await onSubmit(payload);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add model source</DialogTitle>
          <DialogDescription>Register an OpenAI-compatible endpoint and its model IDs.</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-4">
            <ModelSourceFormFields
              control={form.control}
              draft={draft}
              updateDraft={updateDraft}
              apiKeyLabel="Upstream API key"
            />
            <DialogFooter>
              <Button type="submit" disabled={busy || form.formState.isSubmitting}>
                Create
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
