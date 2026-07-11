import type { Control } from "react-hook-form";

import { Checkbox } from "@/components/ui/checkbox";
import { FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import type {
  ModelSourceDraft,
  ModelSourceFormValues,
} from "@/features/model-sources/components/model-source-form";

type ModelSourceFormFieldsProps = {
  control: Control<ModelSourceFormValues>;
  draft: ModelSourceDraft;
  updateDraft: (patch: Partial<ModelSourceDraft>) => void;
  apiKeyLabel: string;
  apiKeyPlaceholder?: string;
};

const CAPABILITY_TOGGLES = [
  ["supportsChatCompletions", "Chat Completions"] as const,
  ["supportsResponses", "Responses"] as const,
  ["supportsAudioTranscriptions", "Audio Transcriptions"] as const,
  ["supportsStreaming", "Streaming"] as const,
  ["supportsTools", "Tools"] as const,
  ["supportsVision", "Vision"] as const,
  ["supportsReasoning", "Reasoning"] as const,
];

export function ModelSourceFormFields({
  control,
  draft,
  updateDraft,
  apiKeyLabel,
  apiKeyPlaceholder,
}: ModelSourceFormFieldsProps) {
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        <FormField
          control={control}
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
          control={control}
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
        control={control}
        name="apiKey"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{apiKeyLabel}</FormLabel>
            <FormControl>
              <Input {...field} type="password" autoComplete="new-password" placeholder={apiKeyPlaceholder} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />

      <FormField
        control={control}
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
        <div className="space-y-1">
          <label className="text-sm font-medium">Context window</label>
          <Input
            value={draft.contextWindow}
            onChange={(event) => updateDraft({ contextWindow: event.target.value })}
            placeholder="e.g. 32768"
            inputMode="numeric"
          />
        </div>
        <div className="space-y-1">
          <label className="text-sm font-medium">Max output tokens</label>
          <Input
            value={draft.maxOutputTokens}
            onChange={(event) => updateDraft({ maxOutputTokens: event.target.value })}
            placeholder="e.g. 4096"
            inputMode="numeric"
          />
        </div>
      </div>

      <div className="space-y-2">
        <div className="text-sm font-medium">Pricing (USD per 1M tokens)</div>
        <p className="text-xs text-muted-foreground">
          Leave blank to skip cost accounting. Applied to every model listed above.
        </p>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Input</label>
            <Input
              value={draft.inputPer1M}
              onChange={(event) => updateDraft({ inputPer1M: event.target.value })}
              placeholder="0.00"
              inputMode="decimal"
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Cached input</label>
            <Input
              value={draft.cachedInputPer1M}
              onChange={(event) => updateDraft({ cachedInputPer1M: event.target.value })}
              placeholder="0.00"
              inputMode="decimal"
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Output</label>
            <Input
              value={draft.outputPer1M}
              onChange={(event) => updateDraft({ outputPer1M: event.target.value })}
              placeholder="0.00"
              inputMode="decimal"
            />
          </div>
        </div>
      </div>

      <div className="space-y-2">
        <div className="text-sm font-medium">Audio pricing (USD per minute)</div>
        <p className="text-xs text-muted-foreground">
          For audio-transcription models billed by duration. Takes precedence over token pricing on
          the transcription route.
        </p>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Per minute</label>
            <Input
              value={draft.audioPerMinute}
              onChange={(event) => updateDraft({ audioPerMinute: event.target.value })}
              placeholder="0.00"
              inputMode="decimal"
            />
          </div>
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {CAPABILITY_TOGGLES.map(([key, label]) => (
          <label key={key} className="flex items-center gap-2 rounded-md border p-2 text-sm">
            <Checkbox
              checked={draft[key]}
              onCheckedChange={(checked) => updateDraft({ [key]: checked === true })}
            />
            {label}
          </label>
        ))}
      </div>
    </>
  );
}
