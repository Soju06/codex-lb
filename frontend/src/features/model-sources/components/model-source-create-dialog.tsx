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
  initialModelSourceDraft,
  createModelSourceFormSchema,
  modelInputsFromForm,
  modelSourceDraftReducer,
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
  const { t } = useTranslation();
  const form = useForm<ModelSourceFormValues>({
    resolver: zodResolver(createModelSourceFormSchema(t)),
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
      supportsEmbeddings: draft.supportsEmbeddings,
      models: modelInputsFromForm(values, draft),
    };
    await onSubmit(payload);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[calc(100dvh-2rem)] flex-col gap-0 overflow-clip p-0 sm:max-w-2xl">
        <DialogHeader className="shrink-0 px-6 pt-6 pr-12 pb-2">
	          <DialogTitle>{t("modelSources.createDialog.title")}</DialogTitle>
	          <DialogDescription>{t("modelSources.createDialog.description")}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)} className="flex min-h-0 flex-1 flex-col">
            <div
              className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-6 pb-4"
              data-testid="model-source-create-scroll-region"
            >
              <ModelSourceFormFields
                control={form.control}
                draft={draft}
                updateDraft={updateDraft}
                apiKeyLabel={t("modelSources.fields.upstreamApiKey")}
              />
            </div>
            <DialogFooter className="shrink-0 border-t px-6 py-4">
              <Button type="submit" disabled={busy || form.formState.isSubmitting}>
	                {t("common.actions.create")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
