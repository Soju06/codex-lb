import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { AccountProbeResponse } from "@/features/accounts/schemas";
import { usePrivacyStore } from "@/hooks/use-privacy";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  accountName: string;
  busy: boolean;
  onRun: (model?: string) => Promise<AccountProbeResponse>;
};

export function AccountTestDialog({ open, onOpenChange, accountName, busy, onRun }: Props) {
  const { t } = useTranslation();
  const blurred = usePrivacyStore((state) => state.blurred);
  const [model, setModel] = useState("");
  const [result, setResult] = useState<AccountProbeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    setResult(null);
    setError(null);
    try {
      setResult(await onRun(model.trim() || undefined));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : t("accounts.toasts.probeFailed"));
    }
  };
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!busy) onOpenChange(next); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("accounts.test.title", { defaultValue: "Test account" })}</DialogTitle>
          <DialogDescription>
            {t("accounts.test.description", {
              defaultValue: "Send a tiny real request using only this account and its configured proxy. This consumes a small amount of quota and waits for a completed response.",
            })}
          </DialogDescription>
        </DialogHeader>
        <p className={`break-all text-sm font-medium${blurred ? " privacy-blur" : ""}`}>{accountName}</p>
        <label className="space-y-2 text-sm">
          <span>{t("accounts.test.model", { defaultValue: "Model (optional)" })}</span>
          <Input value={model} onChange={(event) => setModel(event.target.value)} maxLength={128}
            disabled={busy} placeholder={t("accounts.test.modelPlaceholder", { defaultValue: "Automatic, or enter a model such as gpt-6-astra" })} />
        </label>
        {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
        {result ? (
          <div role="status" className="space-y-2 rounded-md border p-3 text-sm">
            <p className="font-medium">
              {result.outcome === "completed"
                ? t("accounts.test.completed", { defaultValue: "Response completed" })
                : t("accounts.test.failed", { defaultValue: "Response did not complete" })}
            </p>
            <p className="font-mono text-xs">{result.model} · HTTP {result.probeStatusCode ?? "—"} · {result.latencyMs ?? "—"} ms</p>
            {result.errorCode ? <p className="break-all font-mono text-xs">{result.errorCode}</p> : null}
            {result.errorMessage ? <p className="break-words">{result.errorMessage}</p> : null}
            <p className="text-xs text-muted-foreground">{result.accountStatusBefore} → {result.accountStatusAfter}</p>
          </div>
        ) : null}
        <Button disabled={busy} onClick={() => void run()}>
          {busy ? t("accounts.test.running", { defaultValue: "Testing…" }) : t("accounts.test.run", { defaultValue: "Run test" })}
        </Button>
      </DialogContent>
    </Dialog>
  );
}
