import { useReducer } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import type {
  ModelSourceCreateRequest,
  ModelSourceModelInput,
} from "@/features/model-sources/schemas";

const formSchema = z.object({
  name: z.string().min(1, "Name is required"),
  baseUrl: z.string().min(1, "Base URL is required"),
  apiKey: z.string(),
  models: z.string().min(1, "At least one model is required"),
});

type FormValues = z.infer<typeof formSchema>;

type Draft = {
  supportsChatCompletions: boolean;
  supportsResponses: boolean;
  supportsStreaming: boolean;
  supportsTools: boolean;
  supportsVision: boolean;
  contextWindow: string;
  maxOutputTokens: string;
};

const initialDraft: Draft = {
  supportsChatCompletions: true,
  supportsResponses: false,
  supportsStreaming: true,
  supportsTools: false,
  supportsVision: false,
  contextWindow: "",
  maxOutputTokens: "",
};

function draftReducer(state: Draft, patch: Partial<Draft>): Draft {
  return { ...state, ...patch };
}

function parsePositiveInt(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const parsed = Number.parseInt(trimmed, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function modelInputs(values: FormValues, draft: Draft): ModelSourceModelInput[] {
  const contextWindow = parsePositiveInt(draft.contextWindow);
  const maxOutputTokens = parsePositiveInt(draft.maxOutputTokens);
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
      isEnabled: true,
    }));
}

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
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: "",
      baseUrl: "",
      apiKey: "",
      models: "",
    },
  });
  const [draft, updateDraft] = useReducer(draftReducer, initialDraft);

  const handleSubmit = async (values: FormValues) => {
    const payload: ModelSourceCreateRequest = {
      name: values.name,
      baseUrl: values.baseUrl,
      apiKey: values.apiKey.trim() ? values.apiKey.trim() : undefined,
      supportsChatCompletions: draft.supportsChatCompletions,
      supportsResponses: draft.supportsResponses,
      models: modelInputs(values, draft),
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
            <div className="grid gap-3 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input {...field} autoComplete="off" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="baseUrl"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Base URL</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="https://api.example.com/v1" autoComplete="off" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="apiKey"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Upstream API key</FormLabel>
                  <FormControl>
                    <Input {...field} type="password" autoComplete="new-password" />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="models"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Models</FormLabel>
                  <FormControl>
                    <Input {...field} placeholder="deepseek-v4-flash, local-coder" autoComplete="off" />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid gap-3 sm:grid-cols-2">
              <Input
                value={draft.contextWindow}
                onChange={(event) => updateDraft({ contextWindow: event.target.value })}
                placeholder="Context window"
                inputMode="numeric"
              />
              <Input
                value={draft.maxOutputTokens}
                onChange={(event) => updateDraft({ maxOutputTokens: event.target.value })}
                placeholder="Max output tokens"
                inputMode="numeric"
              />
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              {[
                ["supportsChatCompletions", "Chat Completions"] as const,
                ["supportsResponses", "Responses"] as const,
                ["supportsStreaming", "Streaming"] as const,
                ["supportsTools", "Tools"] as const,
                ["supportsVision", "Vision"] as const,
              ].map(([key, label]) => (
                <label key={key} className="flex items-center gap-2 rounded-md border p-2 text-sm">
                  <Checkbox
                    checked={draft[key]}
                    onCheckedChange={(checked) => updateDraft({ [key]: checked === true })}
                  />
                  {label}
                </label>
              ))}
            </div>

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
