import { useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { isEmailLabel } from "@/components/blur-email";
import { Checkbox } from "@/components/ui/checkbox";
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
  commitAccountBundle,
  exportAccountBundle,
  preflightAccountBundle,
} from "@/features/accounts/api";
import type {
  AccountBundleCommitResponse,
  AccountBundlePreflightResponse,
  AccountSummary,
} from "@/features/accounts/schemas";
import { usePrivacyStore } from "@/hooks/use-privacy";

type ExportAccountBundleDialogProps = {
  open: boolean;
  accounts: AccountSummary[];
  onOpenChange: (open: boolean) => void;
};

export function ExportAccountBundleDialog({ open, accounts, onOpenChange }: ExportAccountBundleDialogProps) {
  const { t } = useTranslation();
  const blurred = usePrivacyStore((state) => state.blurred);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set(accounts.map((account) => account.accountId)));
  const [passphrase, setPassphrase] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operationGeneration = useRef(0);
  const selectionEdited = useRef(false);
  const wasOpen = useRef(false);
  const previousOpen = useRef(open);

  useEffect(() => {
    if (!open) {
      wasOpen.current = false;
      return;
    }
    const currentIds = new Set(accounts.map((account) => account.accountId));
    setSelectedIds((current) => {
      const next = wasOpen.current && selectionEdited.current
        ? new Set([...current].filter((accountId) => currentIds.has(accountId)))
        : currentIds;
      if (next.size === current.size && [...next].every((accountId) => current.has(accountId))) {
        return current;
      }
      return next;
    });
    wasOpen.current = true;
  }, [accounts, open]);

  useEffect(() => {
    if (previousOpen.current && !open) {
      operationGeneration.current += 1;
      selectionEdited.current = false;
      setPassphrase("");
      setConfirmation("");
      setBusy(false);
      setError(null);
    }
    previousOpen.current = open;
  }, [open]);

  const reset = () => {
    operationGeneration.current += 1;
    selectionEdited.current = false;
    setPassphrase("");
    setConfirmation("");
    setBusy(false);
    setError(null);
  };
  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) reset();
    onOpenChange(nextOpen);
  };
  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!passphrase || passphrase !== confirmation) return;
    const generation = ++operationGeneration.current;
    setBusy(true);
    setError(null);
    try {
      const blob = await exportAccountBundle([...selectedIds], passphrase);
      if (generation !== operationGeneration.current) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "codex-lb-accounts-v1.clb-account-bundle";
      anchor.click();
      URL.revokeObjectURL(url);
      handleOpenChange(false);
    } catch (caught) {
      if (generation === operationGeneration.current) {
        setError(caught instanceof Error ? caught.message : t("accounts.bundle.error"));
      }
    } finally {
      if (generation === operationGeneration.current) setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[85dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("accounts.bundle.exportTitle")}</DialogTitle>
          <DialogDescription>{t("accounts.bundle.exportDescription")}</DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={handleSubmit}>
          <p className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300">
            {t("accounts.bundle.warning")}
          </p>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>{t("accounts.bundle.accountsLabel")}</Label>
              <Button type="button" variant="link" size="sm" className="h-auto px-0 text-xs" onClick={() => setSelectedIds(new Set(accounts.map((account) => account.accountId)))}>
                {t("accounts.bundle.selectAll")}
              </Button>
            </div>
            <div className="max-h-48 space-y-1 overflow-y-auto rounded-md border p-2">
              {accounts.map((account) => (
                <label key={account.accountId} className="flex cursor-pointer items-center gap-2 rounded p-1.5 text-sm hover:bg-muted/50">
                  <Checkbox
                    checked={selectedIds.has(account.accountId)}
                    onCheckedChange={(checked) => {
                      selectionEdited.current = true;
                      setSelectedIds((current) => {
                        const next = new Set(current);
                        if (checked === true) next.add(account.accountId); else next.delete(account.accountId);
                        return next;
                      });
                    }}
                  />
                  <span className={`min-w-0 truncate${blurred && isEmailLabel(account.displayName, account.email) ? " privacy-blur" : ""}`}>{account.displayName}</span>
                </label>
              ))}
              {accounts.length === 0 ? <p className="p-2 text-xs text-muted-foreground">{t("accounts.bundle.zeroAccounts")}</p> : null}
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="bundle-export-passphrase">{t("accounts.bundle.passphrase")}</Label>
            <Input id="bundle-export-passphrase" type="password" autoComplete="new-password" value={passphrase} onChange={(event) => setPassphrase(event.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="bundle-export-confirm">{t("accounts.bundle.confirmPassphrase")}</Label>
            <Input id="bundle-export-confirm" type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
            {confirmation && passphrase !== confirmation ? <p className="text-xs text-destructive">{t("accounts.bundle.passphraseMismatch")}</p> : null}
          </div>
          {error ? <p role="alert" className="text-xs text-destructive">{error}</p> : null}
          <DialogFooter>
            <Button type="submit" disabled={busy || !passphrase || passphrase !== confirmation}>
              {busy ? t("accounts.bundle.exporting") : t("accounts.bundle.exportAction")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

type ImportAccountBundleDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCommitted: () => Promise<void>;
};

export function ImportAccountBundleDialog({ open, onOpenChange, onCommitted }: ImportAccountBundleDialogProps) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [passphrase, setPassphrase] = useState("");
  const [preflight, setPreflight] = useState<AccountBundlePreflightResponse | null>(null);
  const [conflictMode, setConflictMode] = useState<"skip" | "replace">("skip");
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [result, setResult] = useState<AccountBundleCommitResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operationGeneration = useRef(0);
  const previousOpen = useRef(open);

  useEffect(() => {
    if (previousOpen.current && !open) {
      operationGeneration.current += 1;
      setFile(null);
      setPassphrase("");
      setPreflight(null);
      setConflictMode("skip");
      setConfirmReplace(false);
      setResult(null);
      setBusy(false);
      setError(null);
    }
    previousOpen.current = open;
  }, [open]);

  const reset = () => {
    operationGeneration.current += 1;
    setFile(null);
    setPassphrase("");
    setPreflight(null);
    setConflictMode("skip");
    setConfirmReplace(false);
    setResult(null);
    setBusy(false);
    setError(null);
  };
  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) reset();
    onOpenChange(nextOpen);
  };
  const runPreflight = async (event: FormEvent) => {
    event.preventDefault();
    if (!file || !passphrase) return;
    const generation = ++operationGeneration.current;
    setBusy(true);
    setError(null);
    setConflictMode("skip");
    setConfirmReplace(false);
    try {
      const preview = await preflightAccountBundle(file, passphrase);
      if (generation === operationGeneration.current) setPreflight(preview);
    } catch (caught) {
      if (generation === operationGeneration.current) {
        setError(caught instanceof Error ? caught.message : t("accounts.bundle.error"));
      }
    } finally {
      if (generation === operationGeneration.current) setBusy(false);
    }
  };
  const runCommit = async () => {
    if (!file || !preflight) return;
    const generation = ++operationGeneration.current;
    setBusy(true);
    setError(null);
    try {
      const committed = await commitAccountBundle({
        file,
        passphrase,
        integrityToken: preflight.integrityToken,
        conflictMode,
        confirmReplace,
      });
      if (generation === operationGeneration.current) {
        setResult(committed);
        setPassphrase("");
      }
      try {
        await onCommitted();
      } catch {
        // The bundle is already committed. A failed refresh must not present
        // the durable import as failed or leave the commit action retryable.
      }
    } catch (caught) {
      if (generation === operationGeneration.current) {
        setError(caught instanceof Error ? caught.message : t("accounts.bundle.error"));
      }
    } finally {
      if (generation === operationGeneration.current) setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[85dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("accounts.bundle.importTitle")}</DialogTitle>
          <DialogDescription>{t("accounts.bundle.importDescription")}</DialogDescription>
        </DialogHeader>
        {result ? (
          <div className="space-y-4">
            <output aria-live="polite" className="block text-sm">
              {t("accounts.bundle.resultSummary", result.summary)}
            </output>
            {result.warnings.map((warning) => <p key={warning} className="text-xs text-amber-600">{warning}</p>)}
            <DialogFooter><Button type="button" onClick={() => handleOpenChange(false)}>{t("common.actions.close")}</Button></DialogFooter>
          </div>
        ) : preflight ? (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">{t("accounts.bundle.previewSummary", { newCount: preflight.newCount, matchingCount: preflight.matchingCount })}</p>
            <div className="max-h-56 space-y-2 overflow-y-auto">
              {preflight.accounts.map((account) => (
                <div key={account.index} className="rounded-md border p-2 text-sm">
                  <div className="flex justify-between gap-3"><span>{account.maskedIdentity}</span><span className="text-xs text-muted-foreground">{t(`accounts.bundle.state.${account.state}`)}</span></div>
                  <p className="mt-1 text-xs text-muted-foreground">{account.metadata.alias ?? account.metadata.planType} · {account.metadata.routingPolicy}</p>
                </div>
              ))}
            </div>
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">{t("accounts.bundle.conflictMode")}</legend>
              <label className="flex items-center gap-2 text-sm"><input type="radio" name="bundle-conflict" checked={conflictMode === "skip"} onChange={() => { setConflictMode("skip"); setConfirmReplace(false); }} />{t("accounts.bundle.skip")}</label>
              <label className="flex items-center gap-2 text-sm"><input type="radio" name="bundle-conflict" checked={conflictMode === "replace"} onChange={() => setConflictMode("replace")} />{t("accounts.bundle.replace")}</label>
            </fieldset>
            {conflictMode === "replace" ? (
              <label className="flex items-start gap-2 text-sm text-destructive"><Checkbox checked={confirmReplace} onCheckedChange={(checked) => setConfirmReplace(checked === true)} />{t("accounts.bundle.confirmReplace")}</label>
            ) : null}
            {error ? <p role="alert" className="text-xs text-destructive">{error}</p> : null}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => { setFile(null); setPassphrase(""); setPreflight(null); setConflictMode("skip"); setConfirmReplace(false); setError(null); }}>{t("accounts.bundle.back")}</Button>
              <Button type="button" disabled={busy || (conflictMode === "replace" && !confirmReplace)} onClick={() => void runCommit()}>{busy ? t("accounts.bundle.importing") : t("accounts.bundle.importAction")}</Button>
            </DialogFooter>
          </div>
        ) : (
          <form className="space-y-4" onSubmit={runPreflight}>
            <div className="space-y-2"><Label htmlFor="bundle-import-file">{t("accounts.bundle.file")}</Label><Input id="bundle-import-file" type="file" accept=".clb-account-bundle,application/vnd.codex-lb.account-bundle" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></div>
            <div className="space-y-2"><Label htmlFor="bundle-import-passphrase">{t("accounts.bundle.passphrase")}</Label><Input id="bundle-import-passphrase" type="password" autoComplete="new-password" value={passphrase} onChange={(event) => setPassphrase(event.target.value)} /></div>
            {error ? <p role="alert" className="text-xs text-destructive">{error}</p> : null}
            <DialogFooter><Button type="submit" disabled={busy || !file || !passphrase}>{busy ? t("accounts.bundle.preflighting") : t("accounts.bundle.preflightAction")}</Button></DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
