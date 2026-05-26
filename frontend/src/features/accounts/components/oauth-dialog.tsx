import { Check, CircleAlert, Copy, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import {
  DEFAULT_PROXY_FORM_VALUES,
  validateProxyForm,
  type ProxyFormValues,
} from "@/features/accounts/components/proxy-form-state";
import { ProxyFormSection } from "@/features/accounts/components/proxy-form-section";
import { formatProbeError } from "@/features/accounts/proxy-errors";
import type { AccountProxyInput, OAuthState } from "@/features/accounts/schemas";
import { formatCountdown } from "@/utils/formatters";
import { copyToClipboard } from "@/utils/clipboard";

type Stage = "intro" | "browser" | "device" | "tokens_ready" | "success" | "error";

function getStage(state: OAuthState): Stage {
  if (state.status === "success") return "success";
  if (state.status === "error") return "error";
  if (state.status === "tokens_ready") return "tokens_ready";
  if (state.method === "browser" && (state.status === "pending" || state.status === "starting")) return "browser";
  if (state.method === "device" && (state.status === "pending" || state.status === "starting")) return "device";
  return "intro";
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async (event: MouseEvent<HTMLButtonElement>) => {
    const trigger = event.currentTarget;
    const dialogContainer = trigger.closest("[role='dialog']");
    const blurAfterCopy = event.detail > 0;

    try {
      const copiedToClipboard = await copyToClipboard(text, {
        container: dialogContainer instanceof HTMLElement ? dialogContainer : undefined,
      });
      if (!copiedToClipboard) {
        toast.error("Failed to copy");
        return;
      }

      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Failed to copy");
    } finally {
      if (blurAfterCopy) {
        trigger.blur();
      }
    }
  }, [text]);

  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      className="h-7 cursor-pointer gap-1 px-2 text-xs disabled:cursor-not-allowed"
      onMouseDown={(event) => event.preventDefault()}
      onClick={(event) => void handleCopy(event)}
    >
      {copied ? (
        <>
          <Check className="h-3 w-3" />
          Copied!
        </>
      ) : (
        <>
          <Copy className="h-3 w-3" />
          Copy
        </>
      )}
    </Button>
  );
}

type ManualCallbackInputProps = {
  onSubmit: (callbackUrl: string) => Promise<void>;
  disabled?: boolean;
};

function ManualCallbackInput(props: ManualCallbackInputProps) {
  return <ManualCallbackInputBody key={props.disabled ? "disabled" : "enabled"} {...props} />;
}

function ManualCallbackInputBody({
  onSubmit,
  disabled = false,
}: ManualCallbackInputProps) {
  const [callbackUrl, setCallbackUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = useCallback(async () => {
    if (!callbackUrl.trim()) return;
    setSubmitting(true);
    try {
      await onSubmit(callbackUrl.trim());
      setCallbackUrl("");
    } catch {
      // Parent state renders the error stage/message.
    } finally {
      setSubmitting(false);
    }
  }, [callbackUrl, onSubmit]);

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-muted-foreground">
        Paste callback URL (for remote server)
      </p>
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={callbackUrl}
          onChange={(e) => setCallbackUrl(e.target.value)}
          disabled={disabled}
          placeholder="http://localhost:1455/auth/callback?code=...&state=..."
          className="flex-1 rounded-lg border bg-muted/20 px-3 py-2 font-mono text-xs outline-none focus:ring-1 focus:ring-primary disabled:cursor-not-allowed disabled:opacity-60"
        />
        <Button
          type="button"
          size="sm"
          className="h-8 cursor-pointer px-3 text-xs disabled:cursor-not-allowed"
          disabled={disabled || !callbackUrl.trim() || submitting}
          onClick={() => void handleSubmit()}
        >
          {submitting ? "Submitting..." : "Submit"}
        </Button>
      </div>
    </div>
  );
}

export type OauthDialogProps = {
  open: boolean;
  state: OAuthState;
  onOpenChange: (open: boolean) => void;
  onStart: (
    method: "browser" | "device" | undefined,
    options?: { expectProxy?: boolean; proxy?: AccountProxyInput },
  ) => Promise<void>;
  onComplete: (proxy?: AccountProxyInput) => Promise<void>;
  onManualCallback: (callbackUrl: string) => Promise<void>;
  onReset: () => void;
};

export function OauthDialog({
  open,
  state,
  onOpenChange,
  onStart,
  onComplete,
  onManualCallback,
  onReset,
}: OauthDialogProps) {
  const [selectedMethod, setSelectedMethod] = useState<"browser" | "device">("browser");
  const [showProxy, setShowProxy] = useState(false);
  const [attemptExpectsProxy, setAttemptExpectsProxy] = useState(false);
  const [proxyValues, setProxyValues] = useState<ProxyFormValues>(DEFAULT_PROXY_FORM_VALUES);
  const [finishing, setFinishing] = useState(false);
  const [localFinishError, setLocalFinishError] = useState<string | null>(null);
  const stage = getStage(state);
  const completedRef = useRef(false);
  const browserRefreshInProgress = stage === "browser" && state.status === "starting";

  // Locked at start-time: the expect_proxy flag passed to /api/oauth/start
  // governs whether token-arrival sites defer persistence. After start,
  // toggling ``showProxy`` mid-attempt doesn't change the contract on
  // this attempt — it only affects whether the proxy form is editable.
  const expectingProxy = stage === "tokens_ready" || (stage !== "intro" && attemptExpectsProxy);

  const proxyValidation = useMemo(() => validateProxyForm(proxyValues), [proxyValues]);
  const proxyValidationError = proxyValidation.ok ? null : proxyValidation.error;
  const finishError =
    localFinishError ?? (state.status === "tokens_ready" ? state.errorMessage : null);

  useEffect(() => {
    // Auto-finalize for the no-proxy path: when the user did NOT
    // configure a proxy, status transitions straight to "success" from
    // the polling/manual-callback path (the backend persists at token
    // arrival when expect_proxy=false). Triggering ``onComplete`` here
    // refreshes the parent account list — symmetric with pre-change
    // behavior. With deferred persistence the explicit "Finish setup"
    // button handles the proxy case, so this effect intentionally
    // skips ``tokens_ready``.
    if (stage === "success" && !completedRef.current) {
      completedRef.current = true;
      void onComplete();
    }
    if (stage === "intro") {
      completedRef.current = false;
    }
  }, [stage, onComplete]);

  const close = (next: boolean) => {
    onOpenChange(next);
    if (!next) {
      onReset();
      setSelectedMethod("browser");
      setShowProxy(false);
      setAttemptExpectsProxy(false);
      setProxyValues(DEFAULT_PROXY_FORM_VALUES);
      setFinishing(false);
      setLocalFinishError(null);
    }
  };

  const handleOpenChange = (next: boolean) => {
    if (!next && finishing) return;
    close(next);
  };

  const handleStart = () => {
    const nextExpectProxy = showProxy;
    if (nextExpectProxy && !proxyValidation.ok) return;
    setAttemptExpectsProxy(nextExpectProxy);
    setLocalFinishError(null);
    void onStart(selectedMethod, {
      expectProxy: nextExpectProxy,
      proxy: nextExpectProxy && proxyValidation.ok ? proxyValidation.payload : undefined,
    });
  };

  const handleRefreshBrowserLink = () => {
    if (attemptExpectsProxy && !proxyValidation.ok) return;
    setLocalFinishError(null);
    void onStart("browser", {
      expectProxy: attemptExpectsProxy,
      proxy: attemptExpectsProxy && proxyValidation.ok ? proxyValidation.payload : undefined,
    });
  };

  const handleChangeMethod = () => {
    setLocalFinishError(null);
    setAttemptExpectsProxy(false);
    onReset();
  };

  const handleFinishSetup = async () => {
    if (expectingProxy && !proxyValidation.ok) return;
    setFinishing(true);
    setLocalFinishError(null);
    completedRef.current = true;
    try {
      await onComplete(expectingProxy && proxyValidation.ok ? proxyValidation.payload : undefined);
    } catch (error) {
      setLocalFinishError(formatProbeError(error));
    } finally {
      setFinishing(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {stage === "success" ? "Account added" : stage === "error" ? "Authorization failed" : "Add account with OAuth"}
          </DialogTitle>
          {stage === "intro" ? (
            <DialogDescription>Choose a sign-in method and complete authorization.</DialogDescription>
          ) : null}
        </DialogHeader>

        {/* Intro stage */}
        {stage === "intro" ? (
          <div className="space-y-3">
            <div className="space-y-2">
              <button
                type="button"
                onClick={() => setSelectedMethod("browser")}
                className={cn(
                  "w-full cursor-pointer rounded-lg border p-3 text-left transition-colors",
                  selectedMethod === "browser"
                    ? "border-primary bg-primary/5"
                    : "hover:bg-muted/50",
                )}
              >
                <p className="text-sm font-medium">Browser (PKCE)</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Opens a browser window for sign-in. Recommended for most users.
                </p>
              </button>
              <button
                type="button"
                onClick={() => setSelectedMethod("device")}
                className={cn(
                  "w-full cursor-pointer rounded-lg border p-3 text-left transition-colors",
                  selectedMethod === "device"
                    ? "border-primary bg-primary/5"
                    : "hover:bg-muted/50",
                )}
              >
                <p className="text-sm font-medium">Device code</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Use a code on another device. Useful for headless environments.
                </p>
              </button>
            </div>
            <ProxyFormSection
              idPrefix="oauth"
              values={proxyValues}
              onChange={setProxyValues}
              showProxy={showProxy}
              onToggleShowProxy={setShowProxy}
              errorMessage={showProxy ? proxyValidationError : null}
            />
          </div>
        ) : null}

        {/* Browser stage */}
        {stage === "browser" && expectingProxy ? (
          <div className="space-y-2">
            <ProxyFormSection
              idPrefix="oauth"
              values={proxyValues}
              onChange={setProxyValues}
              showProxy
              onToggleShowProxy={() => undefined}
              toggleDisabled
              disabled={browserRefreshInProgress}
              errorMessage={proxyValidationError}
            />
          </div>
        ) : null}
        {stage === "browser" ? (
          <div className="min-w-0 space-y-3 text-sm">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-medium text-muted-foreground">Authorization URL</p>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-7 cursor-pointer gap-1 px-2 text-xs disabled:cursor-not-allowed"
                  disabled={browserRefreshInProgress || (expectingProxy && !proxyValidation.ok)}
                  onClick={handleRefreshBrowserLink}
                >
                  {browserRefreshInProgress ? (
                    <>
                      <Loader2 className="h-3 w-3 animate-spin" />
                      Refreshing...
                    </>
                  ) : (
                    <>
                      <RefreshCw className="h-3 w-3" />
                      Refresh link
                    </>
                  )}
                </Button>
              </div>
              {browserRefreshInProgress ? (
                <div className="flex items-center gap-2 rounded-lg border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  <span>Generating a fresh sign-in link...</span>
                </div>
              ) : state.authorizationUrl ? (
                <div className="flex min-w-0 items-center gap-2 rounded-lg border bg-muted/20 px-3 py-2">
                  <p className="min-w-0 flex-1 truncate font-mono text-xs">{state.authorizationUrl}</p>
                  <CopyButton text={state.authorizationUrl} />
                </div>
              ) : null}
              <p className="text-xs text-muted-foreground">
                Refresh the link if the current sign-in page has already been used.
              </p>
            </div>
            <ManualCallbackInput onSubmit={onManualCallback} disabled={browserRefreshInProgress} />
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              <span>Waiting for authorization to complete...</span>
            </div>
          </div>
        ) : null}

        {/* Device stage */}
        {stage === "device" && expectingProxy ? (
          <div className="space-y-2">
            <ProxyFormSection
              idPrefix="oauth"
              values={proxyValues}
              onChange={setProxyValues}
              showProxy
              onToggleShowProxy={() => undefined}
              toggleDisabled
              errorMessage={proxyValidationError}
            />
          </div>
        ) : null}
        {stage === "device" ? (
          <div className="space-y-3 text-sm">
            <ol className="list-inside list-decimal space-y-1 text-xs text-muted-foreground">
              <li>Open the verification link below</li>
              <li>Enter the user code when prompted</li>
              <li>Complete sign-in on that page</li>
            </ol>

            {state.userCode ? (
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-muted-foreground">User code</p>
                <div className="flex items-center gap-2 rounded-lg border bg-muted/20 px-3 py-2">
                  <p className="min-w-0 flex-1 font-mono text-lg font-bold tracking-widest">{state.userCode}</p>
                  <CopyButton text={state.userCode} />
                </div>
              </div>
            ) : null}

            {state.verificationUrl ? (
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-muted-foreground">Verification URL</p>
                <div className="flex min-w-0 items-center gap-2 overflow-hidden rounded-lg border bg-muted/20 px-3 py-2">
                  <p className="min-w-0 flex-1 truncate break-all font-mono text-xs">{state.verificationUrl}</p>
                  <CopyButton text={state.verificationUrl} />
                </div>
              </div>
            ) : null}

            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              <span>
                Waiting for authorization
                {state.expiresInSeconds != null && state.expiresInSeconds > 0
                  ? ` · expires in ${formatCountdown(state.expiresInSeconds)}`
                  : "..."}
              </span>
            </div>
          </div>
        ) : null}

        {/* Tokens-ready stage: deferred-persistence finalization */}
        {stage === "tokens_ready" ? (
          <div className="space-y-3">
            <div className="flex items-start gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-400">
              <Check className="mt-0.5 h-4 w-4 shrink-0" />
              <p>
                Sign-in complete. Configure the proxy below, then click Finish to validate
                and save the account.
              </p>
            </div>
            <ProxyFormSection
              idPrefix="oauth"
              values={proxyValues}
              onChange={setProxyValues}
              showProxy
              onToggleShowProxy={() => undefined}
              toggleDisabled
              disabled={finishing}
              errorMessage={proxyValidationError}
            />
            {finishError ? (
              <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
                <p>{finishError}</p>
              </div>
            ) : null}
          </div>
        ) : null}

        {/* Success stage */}
        {stage === "success" ? (
          <div className="flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-3 text-sm text-emerald-700 dark:text-emerald-400">
            <Check className="h-4 w-4 shrink-0" />
            <p>Account has been added successfully.</p>
          </div>
        ) : null}

        {/* Error stage */}
        {stage === "error" ? (
          <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-3 text-sm text-destructive">
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <p>{state.errorMessage || "An unknown error occurred."}</p>
          </div>
        ) : null}

        <DialogFooter>
          {stage === "intro" ? (
            <>
              <Button
                type="button"
                variant="outline"
                className="cursor-pointer disabled:cursor-not-allowed"
                onClick={() => close(false)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                className="cursor-pointer disabled:cursor-not-allowed"
                disabled={showProxy && !proxyValidation.ok}
                onClick={handleStart}
              >
                Start sign-in
              </Button>
            </>
          ) : null}

          {stage === "browser" ? (
            <>
              <Button
                type="button"
                variant="outline"
                className="cursor-pointer disabled:cursor-not-allowed"
                disabled={browserRefreshInProgress}
                onClick={handleChangeMethod}
              >
                Change method
              </Button>
              {state.authorizationUrl && !browserRefreshInProgress ? (
                <Button
                  type="button"
                  className="cursor-pointer disabled:cursor-not-allowed"
                  asChild
                >
                  <a href={state.authorizationUrl} target="_blank" rel="noreferrer">
                    <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                    Open sign-in page
                  </a>
                </Button>
              ) : null}
            </>
          ) : null}

          {stage === "device" ? (
            <>
              <Button
                type="button"
                variant="outline"
                className="cursor-pointer disabled:cursor-not-allowed"
                onClick={handleChangeMethod}
              >
                Change method
              </Button>
              {state.verificationUrl ? (
                <Button
                  type="button"
                  className="cursor-pointer disabled:cursor-not-allowed"
                  asChild
                >
                  <a href={state.verificationUrl} target="_blank" rel="noreferrer">
                    <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                    Open link
                  </a>
                </Button>
              ) : null}
            </>
          ) : null}

          {stage === "tokens_ready" ? (
            <>
              <Button
                type="button"
                variant="outline"
                className="cursor-pointer disabled:cursor-not-allowed"
                disabled={finishing}
                onClick={() => close(false)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                className="cursor-pointer disabled:cursor-not-allowed"
                disabled={finishing || (expectingProxy && !proxyValidation.ok)}
                onClick={() => void handleFinishSetup()}
              >
                {finishing
                  ? expectingProxy
                    ? "Validating proxy & saving…"
                    : "Saving…"
                  : expectingProxy
                    ? "Finish setup & validate proxy"
                    : "Finish setup"}
              </Button>
            </>
          ) : null}

          {stage === "success" ? (
            <Button
              type="button"
              className="cursor-pointer disabled:cursor-not-allowed"
              onClick={() => close(false)}
            >
              Done
            </Button>
          ) : null}

          {stage === "error" ? (
            <>
              <Button
                type="button"
                variant="outline"
                className="cursor-pointer disabled:cursor-not-allowed"
                onClick={handleChangeMethod}
              >
                Try again
              </Button>
              <Button
                type="button"
                className="cursor-pointer disabled:cursor-not-allowed"
                onClick={() => close(false)}
              >
                Close
              </Button>
            </>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
