import { zodResolver } from "@hookform/resolvers/zod";
import { Save } from "lucide-react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Form, FormField } from "@/components/ui/form";
import { Switch } from "@/components/ui/switch";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

const formSchema = z.object({
  stickyThreadsEnabled: z.boolean(),
  preferEarlierResetAccounts: z.boolean(),
});

type FormValues = z.infer<typeof formSchema>;

export type RoutingSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

export function RoutingSettings({ settings, busy, onSave }: RoutingSettingsProps) {
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      stickyThreadsEnabled: settings.stickyThreadsEnabled,
      preferEarlierResetAccounts: settings.preferEarlierResetAccounts,
    },
  });

  const handleSubmit = (values: FormValues) => {
    void onSave({
      ...values,
      totpRequiredOnLogin: settings.totpRequiredOnLogin,
      apiKeyAuthEnabled: settings.apiKeyAuthEnabled,
    });
  };

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="mb-4">
        <h3 className="text-sm font-semibold">Routing</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">Control how requests are distributed across accounts.</p>
      </div>

      <Form {...form}>
        <form onSubmit={form.handleSubmit(handleSubmit)}>
          <div className="space-y-1">
            <FormField
              control={form.control}
              name="stickyThreadsEnabled"
              render={({ field }) => (
                <div className="flex items-center justify-between rounded-lg p-3 transition-colors hover:bg-muted/40">
                  <div>
                    <p className="text-sm font-medium">Sticky threads</p>
                    <p className="text-xs text-muted-foreground">Keep related requests on the same account.</p>
                  </div>
                  <Switch checked={field.value} onCheckedChange={field.onChange} disabled={busy} />
                </div>
              )}
            />

            <FormField
              control={form.control}
              name="preferEarlierResetAccounts"
              render={({ field }) => (
                <div className="flex items-center justify-between rounded-lg p-3 transition-colors hover:bg-muted/40">
                  <div>
                    <p className="text-sm font-medium">Prefer earlier reset</p>
                    <p className="text-xs text-muted-foreground">Bias traffic to accounts with earlier quota reset.</p>
                  </div>
                  <Switch checked={field.value} onCheckedChange={field.onChange} disabled={busy} />
                </div>
              )}
            />
          </div>

          <div className="mt-4 border-t pt-4">
            <Button
              type="submit"
              size="sm"
              className="h-8 gap-1.5 text-xs"
              disabled={busy}
            >
              <Save className="h-3.5 w-3.5" />
              Save routing settings
            </Button>
          </div>
        </form>
      </Form>
    </section>
  );
}
