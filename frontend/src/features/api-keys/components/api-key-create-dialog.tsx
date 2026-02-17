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
import { ExpiryPicker } from "@/features/api-keys/components/expiry-picker";
import { LimitRulesEditor } from "@/features/api-keys/components/limit-rules-editor";
import { ModelMultiSelect } from "@/features/api-keys/components/model-multi-select";
import type { ApiKeyCreateRequest, LimitRuleCreate } from "@/features/api-keys/schemas";

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

export function ApiKeyCreateDialog({ open, busy, onOpenChange, onSubmit }: ApiKeyCreateDialogProps) {
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: { name: "" },
  });

  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [limitRules, setLimitRules] = useState<LimitRuleCreate[]>([]);
  const [expiresAt, setExpiresAt] = useState<Date | null>(null);

  const handleSubmit = async (values: FormValues) => {
    const validLimits = limitRules.filter((r) => r.maxValue > 0);
    const payload: ApiKeyCreateRequest = {
      name: values.name,
      allowedModels: selectedModels.length > 0 ? selectedModels : undefined,
      expiresAt: expiresAt?.toISOString(),
      limits: validLimits.length > 0 ? validLimits : undefined,
    };
    await onSubmit(payload);
    form.reset();
    setSelectedModels([]);
    setLimitRules([]);
    setExpiresAt(null);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Create API key</DialogTitle>
          <DialogDescription>Set restrictions and expiration for this key.</DialogDescription>
        </DialogHeader>

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
      </DialogContent>
    </Dialog>
  );
}
