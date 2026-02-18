import { useMemo, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
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
import { Switch } from "@/components/ui/switch";
import { ExpiryPicker } from "@/features/api-keys/components/expiry-picker";
import { LimitRulesEditor } from "@/features/api-keys/components/limit-rules-editor";
import { ModelMultiSelect } from "@/features/api-keys/components/model-multi-select";
import type { ApiKey, ApiKeyUpdateRequest, LimitRuleCreate, LimitType } from "@/features/api-keys/schemas";
import { parseDate } from "@/utils/formatters";

import { hasLimitRuleChanges, normalizeLimitRules } from "./limit-rules-utils";

const formSchema = z.object({
  name: z.string().min(1, "Name is required"),
  isActive: z.boolean(),
});

type FormValues = z.infer<typeof formSchema>;

export type ApiKeyEditDialogProps = {
  open: boolean;
  busy: boolean;
  apiKey: ApiKey | null;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: ApiKeyUpdateRequest) => Promise<void>;
};

type ApiKeyEditFormProps = {
  apiKey: ApiKey;
  busy: boolean;
  onSubmit: (payload: ApiKeyUpdateRequest) => Promise<void>;
  onClose: () => void;
};

function limitsToCreateRules(apiKey: ApiKey): LimitRuleCreate[] {
  return apiKey.limits.map((l) => ({
    limitType: l.limitType,
    limitWindow: l.limitWindow,
    maxValue: l.maxValue,
    modelFilter: l.modelFilter,
  }));
}

function ApiKeyEditForm({ apiKey, busy, onSubmit, onClose }: ApiKeyEditFormProps) {
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: apiKey.name,
      isActive: apiKey.isActive,
    },
  });

  const [selectedModels, setSelectedModels] = useState<string[]>(apiKey.allowedModels || []);
  const initialLimitRules = useMemo(() => limitsToCreateRules(apiKey), [apiKey]);
  const [limitRules, setLimitRules] = useState<LimitRuleCreate[]>(() => initialLimitRules);
  const [expiresAt, setExpiresAt] = useState<Date | null>(() => parseDate(apiKey.expiresAt));

  const handleSubmit = async (values: FormValues) => {
    const normalizedLimits = normalizeLimitRules(limitRules);
    const payload: ApiKeyUpdateRequest = {
      name: values.name,
      allowedModels: selectedModels.length > 0 ? selectedModels : null,
      expiresAt: expiresAt?.toISOString() ?? null,
      isActive: values.isActive,
    };
    if (hasLimitRuleChanges(initialLimitRules, limitRules)) {
      payload.limits = normalizedLimits;
    }
    await onSubmit(payload);
    onClose();
  };

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(handleSubmit)}>
        <div className="grid gap-x-6 sm:grid-cols-2">
          {/* Left column — General */}
          <div className="max-h-[55vh] space-y-3 overflow-y-auto overscroll-contain pl-1 pr-2">
            <h4 className="sticky top-0 bg-background pb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">General</h4>

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

            <div className="space-y-1">
              <label className="text-sm font-medium">Allowed models</label>
              <ModelMultiSelect value={selectedModels} onChange={setSelectedModels} />
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium">Expiry</label>
              <ExpiryPicker value={expiresAt} onChange={setExpiresAt} />
            </div>

            <FormField
              control={form.control}
              name="isActive"
              render={({ field }) => (
                <div className="flex items-center justify-between rounded-md border p-2">
                  <span className="text-sm">Active</span>
                  <Switch checked={field.value} onCheckedChange={field.onChange} />
                </div>
              )}
            />
          </div>

          {/* Right column — Limits */}
          <div className="max-h-[55vh] space-y-3 overflow-y-auto overscroll-contain pl-1 pr-2 max-sm:mt-3 max-sm:border-t max-sm:pt-3">
            <h4 className="sticky top-0 bg-background pb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Limits</h4>
            <LimitRulesEditor rules={limitRules} onChange={setLimitRules} />

            {apiKey.limits.length > 0 ? (
              <div className="space-y-1">
                <label className="text-xs font-medium text-muted-foreground">Current usage</label>
                <div className="space-y-1">
                  {apiKey.limits.map((limit) => (
                    <LimitUsageBar key={limit.id} limit={limit} />
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </div>

        <DialogFooter className="mt-4">
          <Button type="submit" disabled={busy || form.formState.isSubmitting}>
            Save
          </Button>
        </DialogFooter>
      </form>
    </Form>
  );
}

function LimitUsageBar({ limit }: { limit: ApiKey["limits"][number] }) {
  const isCost = limit.limitType === "cost_usd";
  const percent = limit.maxValue > 0 ? Math.min(100, (limit.currentValue / limit.maxValue) * 100) : 0;
  const current = isCost ? `$${(limit.currentValue / 1_000_000).toFixed(2)}` : formatTokenCount(limit.currentValue);
  const max = isCost ? `$${(limit.maxValue / 1_000_000).toFixed(2)}` : formatTokenCount(limit.maxValue);
  const typeLabel = LIMIT_TYPE_SHORT[limit.limitType];
  const windowLabel = limit.limitWindow;
  const modelLabel = limit.modelFilter || "all";

  return (
    <div className="rounded border p-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">
          {typeLabel} ({windowLabel}, {modelLabel})
        </span>
        <span className="tabular-nums">
          {current} / {max}
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full rounded-full bg-muted">
        <div
          className={`h-full rounded-full ${percent >= 90 ? "bg-destructive" : "bg-primary"}`}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

const LIMIT_TYPE_SHORT: Record<LimitType, string> = {
  total_tokens: "Tokens",
  input_tokens: "Input",
  output_tokens: "Output",
  cost_usd: "Cost",
};

function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function ApiKeyEditDialog({ open, busy, apiKey, onOpenChange, onSubmit }: ApiKeyEditDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Edit API key</DialogTitle>
          <DialogDescription>Update restrictions and lifecycle settings.</DialogDescription>
        </DialogHeader>

        {apiKey ? (
          <ApiKeyEditForm
            key={`${apiKey.id}:${open ? "open" : "closed"}`}
            apiKey={apiKey}
            busy={busy}
            onSubmit={onSubmit}
            onClose={() => onOpenChange(false)}
          />
        ) : (
          <p className="text-sm text-muted-foreground">Select an API key to edit.</p>
        )}
      </DialogContent>
    </Dialog>
  );
}
