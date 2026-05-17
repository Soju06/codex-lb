import { useState } from "react";
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
import { Checkbox } from "@/components/ui/checkbox";
import { ExpiryPicker } from "@/features/api-keys/components/expiry-picker";
import { LimitRulesEditor } from "@/features/api-keys/components/limit-rules-editor";
import { ModelMultiSelect } from "@/features/api-keys/components/model-multi-select";
import type { ApiKeyCreateRequest, LimitRuleCreate, ServiceTierType } from "@/features/api-keys/schemas";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const formSchema = z.object({
  name: z.string().min(1, "Name is required"),
});

type FormValues = z.infer<typeof formSchema>;

export type ApiKeyCreateDialogProps = {
  open: boolean;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: ApiKeyCreateRequest) => Promise<void>;
};

type ApiKeyCreateFormProps = {
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: ApiKeyCreateRequest) => Promise<void>;
};

function ApiKeyCreateForm({ busy, onClose, onSubmit }: ApiKeyCreateFormProps) {
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: { name: "" },
  });

  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [limitRules, setLimitRules] = useState<LimitRuleCreate[]>([]);
  const [expiresAt, setExpiresAt] = useState<Date | null>(null);
  const [enforcedModel, setEnforcedModel] = useState("");
  const [enforcedReasoningEffort, setEnforcedReasoningEffort] = useState("none");
  const [enforcedServiceTier, setEnforcedServiceTier] = useState("none");
  const [applyToCodexModel, setApplyToCodexModel] = useState(false);

  const handleSubmit = async (values: FormValues) => {
    const validLimits = limitRules.filter((r) => r.maxValue > 0);
    const payload: ApiKeyCreateRequest = {
      name: values.name,
      allowedModels: selectedModels.length > 0 ? selectedModels : undefined,
      applyToCodexModel,
      enforcedModel: enforcedModel.trim() ? enforcedModel.trim() : null,
      enforcedReasoningEffort: enforcedReasoningEffort === "none" ? null : enforcedReasoningEffort as "minimal" | "low" | "medium" | "high" | "xhigh",
      enforcedServiceTier: enforcedServiceTier === "none" ? null : enforcedServiceTier as ServiceTierType,
      expiresAt: expiresAt?.toISOString(),
      limits: validLimits.length > 0 ? validLimits : undefined,
    };
    try {
      await onSubmit(payload);
    } catch {
      return;
    }
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

            <div className="flex items-center gap-2 rounded-md border p-2 text-sm">
              <Checkbox
                id="create-api-key-apply-to-codex-model"
                checked={applyToCodexModel}
                onCheckedChange={(checked) => setApplyToCodexModel(checked === true)}
              />
              <label htmlFor="create-api-key-apply-to-codex-model" className="cursor-pointer">
                Apply to codex /model
              </label>
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium">Enforced model</label>
              <Input
                value={enforcedModel}
                onChange={(e) => setEnforcedModel(e.target.value)}
                placeholder="e.g. gpt-5.3-codex"
                autoComplete="off"
              />
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium">Enforced reasoning</label>
              <Select value={enforcedReasoningEffort} onValueChange={setEnforcedReasoningEffort}>
                <SelectTrigger>
                  <SelectValue placeholder="None" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">None</SelectItem>
                  <SelectItem value="minimal">Minimal</SelectItem>
                  <SelectItem value="low">Low</SelectItem>
                  <SelectItem value="medium">Medium</SelectItem>
                  <SelectItem value="high">High</SelectItem>
                  <SelectItem value="xhigh">XHigh</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium">Enforced service tier</label>
              <Select value={enforcedServiceTier} onValueChange={setEnforcedServiceTier}>
                <SelectTrigger>
                  <SelectValue placeholder="None" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">None</SelectItem>
                  <SelectItem value="auto">Auto</SelectItem>
                  <SelectItem value="default">Default</SelectItem>
                  <SelectItem value="priority">Priority</SelectItem>
                  <SelectItem value="flex">Flex</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium">Expiry</label>
              <ExpiryPicker value={expiresAt} onChange={setExpiresAt} />
            </div>
          </div>

          {/* Right column — Limits */}
          <div className="max-h-[55vh] space-y-3 overflow-y-auto overscroll-contain pl-1 pr-2 max-sm:mt-3 max-sm:border-t max-sm:pt-3">
            <h4 className="sticky top-0 bg-background pb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Limits</h4>
            <LimitRulesEditor rules={limitRules} onChange={setLimitRules} />
          </div>
        </div>

        <DialogFooter className="mt-4">
          <Button type="submit" disabled={busy || form.formState.isSubmitting}>
            Create
          </Button>
        </DialogFooter>
      </form>
    </Form>
  );
}

export function ApiKeyCreateDialog({ open, busy, onOpenChange, onSubmit }: ApiKeyCreateDialogProps) {

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent className="sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>Create API key</DialogTitle>
            <DialogDescription>Set restrictions and expiration for this key.</DialogDescription>
          </DialogHeader>
          <ApiKeyCreateForm busy={busy} onClose={() => onOpenChange(false)} onSubmit={onSubmit} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
