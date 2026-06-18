import { useState } from "react";
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
  UpstreamProxyEndpoint,
  UpstreamProxyPoolCreateRequest,
} from "@/features/settings/schemas";

const formSchema = z.object({
  name: z.string().trim().min(1, "Name is required"),
});

type FormValues = z.infer<typeof formSchema>;

export type ProxyPoolCreateDialogProps = {
  open: boolean;
  busy: boolean;
  endpoints: UpstreamProxyEndpoint[];
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: UpstreamProxyPoolCreateRequest) => Promise<unknown>;
};

type ProxyPoolCreateFormProps = {
  busy: boolean;
  endpoints: UpstreamProxyEndpoint[];
  onClose: () => void;
  onSubmit: (payload: UpstreamProxyPoolCreateRequest) => Promise<unknown>;
};

function ProxyPoolCreateForm({ busy, endpoints, onClose, onSubmit }: ProxyPoolCreateFormProps) {
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: { name: "" },
  });
  const [selectedEndpointIds, setSelectedEndpointIds] = useState<Set<string>>(new Set());

  const toggleEndpoint = (endpointId: string, checked: boolean) => {
    setSelectedEndpointIds((current) => {
      const next = new Set(current);
      if (checked) {
        next.add(endpointId);
      } else {
        next.delete(endpointId);
      }
      return next;
    });
  };

  const handleSubmit = async (values: FormValues) => {
    const payload: UpstreamProxyPoolCreateRequest = {
      name: values.name.trim(),
      endpointIds: [...selectedEndpointIds],
      isActive: true,
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
      <form className="space-y-4" onSubmit={form.handleSubmit(handleSubmit)}>
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Pool name</FormLabel>
              <FormControl>
                <Input {...field} autoComplete="off" placeholder="Codex pool" />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <div className="space-y-1.5">
          <p className="text-sm font-medium">Endpoints</p>
          <p className="text-xs text-muted-foreground">
            Select the endpoints this pool routes through. You can add more later.
          </p>
          <div className="max-h-48 space-y-2 overflow-y-auto overscroll-contain rounded-md border p-2">
            {endpoints.length === 0 ? (
              <p className="text-xs text-muted-foreground">Create an endpoint first.</p>
            ) : (
              endpoints.map((endpoint) => (
                <label
                  key={endpoint.id}
                  className="flex cursor-pointer items-center gap-2 rounded-md px-1 py-1 text-xs hover:bg-muted/50"
                >
                  <Checkbox
                    checked={selectedEndpointIds.has(endpoint.id)}
                    disabled={busy}
                    onCheckedChange={(checked) => toggleEndpoint(endpoint.id, checked === true)}
                  />
                  <span className="min-w-0 truncate">
                    <span className="font-medium text-foreground">{endpoint.name}</span>
                    <span className="text-muted-foreground">
                      {" "}
                      · {endpoint.scheme}://{endpoint.host}:{endpoint.port}
                    </span>
                  </span>
                </label>
              ))
            )}
          </div>
        </div>

        <DialogFooter className="mt-2">
          <Button type="submit" disabled={busy || form.formState.isSubmitting}>
            Create pool
          </Button>
        </DialogFooter>
      </form>
    </Form>
  );
}

export function ProxyPoolCreateDialog({ open, busy, endpoints, onOpenChange, onSubmit }: ProxyPoolCreateDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Create proxy pool</DialogTitle>
            <DialogDescription>
              Group endpoints into a pool that accounts and the default route can bind to.
            </DialogDescription>
          </DialogHeader>
          <ProxyPoolCreateForm
            busy={busy}
            endpoints={endpoints}
            onClose={() => onOpenChange(false)}
            onSubmit={onSubmit}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
