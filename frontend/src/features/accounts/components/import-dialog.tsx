import { useMemo, useState } from "react";
import type { FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  DEFAULT_PROXY_FORM_VALUES,
  validateProxyForm,
  type ProxyFormValues,
} from "@/features/accounts/components/proxy-form-state";
import { ProxyFormSection } from "@/features/accounts/components/proxy-form-section";
import { type AccountImportResponse } from "@/features/accounts/schemas";
import type { AccountProxyInput } from "@/features/accounts/schemas";

export type ImportDialogProps = {
  open: boolean;
  busy: boolean;
  error: string | null;
  onOpenChange: (open: boolean) => void;
  onImport: (file: File, proxy?: AccountProxyInput) => Promise<AccountImportResponse>;
};

export function ImportDialog({
  open,
  busy,
  error,
  onOpenChange,
  onImport,
}: ImportDialogProps) {
  return (
    <Dialog open={open} onOpenChange={busy ? undefined : onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Import auth.json</DialogTitle>
          <DialogDescription>Upload an exported account auth.json file.</DialogDescription>
        </DialogHeader>
        <ImportForm
          key={open ? "open" : "closed"}
          busy={busy}
          error={error}
          onImport={onImport}
          onDone={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}

type ImportFormProps = {
  busy: boolean;
  error: string | null;
  onImport: (file: File, proxy?: AccountProxyInput) => Promise<AccountImportResponse>;
  onDone: () => void;
};

function ImportForm({ busy, error, onImport, onDone }: ImportFormProps) {
  const [file, setFile] = useState<File | null>(null);
  const [showProxy, setShowProxy] = useState(false);
  const [proxyValues, setProxyValues] = useState<ProxyFormValues>(DEFAULT_PROXY_FORM_VALUES);

  const proxyValidation = useMemo(() => validateProxyForm(proxyValues), [proxyValues]);
  const proxyRequested = showProxy;
  const proxyValidationError = proxyValidation.ok ? null : proxyValidation.error;
  const submitting = busy;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!file) return;

    try {
      if (proxyRequested && !proxyValidation.ok) {
        return;
      }

      const proxyPayload: AccountProxyInput | undefined =
        proxyRequested && proxyValidation.ok ? proxyValidation.payload : undefined;
      await onImport(file, proxyPayload);
      onDone();
    } catch {
      // The parent mutation owns import error rendering.
    }
  };

  return (
    <form className="space-y-4" onSubmit={handleSubmit}>
      <div className="space-y-2">
        <Label htmlFor="auth-json-file">File</Label>
        <Input
          id="auth-json-file"
          type="file"
          accept="application/json,.json"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          disabled={submitting}
        />
      </div>

      <ProxyFormSection
        idPrefix="import"
        values={proxyValues}
        onChange={setProxyValues}
        showProxy={showProxy}
        onToggleShowProxy={setShowProxy}
        disabled={submitting}
        errorMessage={proxyRequested ? proxyValidationError : null}
      />

      {error ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 text-xs text-destructive">
          {error}
        </p>
      ) : null}

      <DialogFooter>
        <Button type="submit" disabled={submitting || !file || (proxyRequested && !proxyValidation.ok)}>
          {submitting
            ? proxyRequested
              ? "Importing & validating proxy…"
              : "Importing…"
            : proxyRequested
              ? "Import & validate proxy"
              : "Import"}
        </Button>
      </DialogFooter>
    </form>
  );
}
