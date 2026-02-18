import { Check, CircleAlert, Copy, ExternalLink, Loader2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

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
import type { OAuthState } from "@/features/accounts/schemas";
import { formatCountdown } from "@/utils/formatters";

type Stage = "intro" | "browser" | "device" | "success" | "error";

function getStage(state: OAuthState): Stage {
  if (state.status === "success") return "success";
  if (state.status === "error") return "error";
  if (state.method === "browser" && (state.status === "pending" || state.status === "starting")) return "browser";
  if (state.method === "device" && (state.status === "pending" || state.status === "starting")) return "device";
  return "intro";
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [text]);

  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      className="h-7 gap-1 px-2 text-xs"
      onClick={() => void handleCopy()}
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

export type OauthDialogProps = {
  open: boolean;
  state: OAuthState;
  onOpenChange: (open: boolean) => void;
  onStart: (method?: "browser" | "device") => Promise<void>;
  onComplete: () => Promise<void>;
  onReset: () => void;
};

export function OauthDialog({
  open,
  state,
  onOpenChange,
  onStart,
  onComplete,
  onReset,
}: OauthDialogProps) {
  const [selectedMethod, setSelectedMethod] = useState<"browser" | "device">("browser");
  const stage = getStage(state);
  const completedRef = useRef(false);

  useEffect(() => {
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
    }
  };

  const handleStart = () => {
    void onStart(selectedMethod);
  };

  const handleChangeMethod = () => {
    onReset();
  };

  return (
    <Dialog open={open} onOpenChange={close}>
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
          <div className="space-y-2">
            <button
              type="button"
              onClick={() => setSelectedMethod("browser")}
              className={cn(
                "w-full rounded-lg border p-3 text-left transition-colors",
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
                "w-full rounded-lg border p-3 text-left transition-colors",
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
        ) : null}

        {/* Browser stage */}
        {stage === "browser" ? (
          <div className="min-w-0 space-y-3 text-sm">
            {state.authorizationUrl ? (
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-muted-foreground">Authorization URL</p>
                <div className="flex min-w-0 items-center gap-2 rounded-lg border bg-muted/20 px-3 py-2">
                  <p className="min-w-0 flex-1 truncate font-mono text-xs">{state.authorizationUrl}</p>
                  <CopyButton text={state.authorizationUrl} />
                </div>
              </div>
            ) : null}
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              <span>Waiting for authorization to complete...</span>
            </div>
          </div>
        ) : null}

        {/* Device stage */}
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
              <Button type="button" variant="outline" onClick={() => close(false)}>
                Cancel
              </Button>
              <Button type="button" onClick={handleStart}>
                Start sign-in
              </Button>
            </>
          ) : null}

          {stage === "browser" ? (
            <>
              <Button type="button" variant="outline" onClick={handleChangeMethod}>
                Change method
              </Button>
              {state.authorizationUrl ? (
                <Button type="button" asChild>
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
              <Button type="button" variant="outline" onClick={handleChangeMethod}>
                Change method
              </Button>
              {state.verificationUrl ? (
                <Button type="button" asChild>
                  <a href={state.verificationUrl} target="_blank" rel="noreferrer">
                    <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                    Open link
                  </a>
                </Button>
              ) : null}
            </>
          ) : null}

          {stage === "success" ? (
            <Button type="button" onClick={() => close(false)}>
              Done
            </Button>
          ) : null}

          {stage === "error" ? (
            <>
              <Button type="button" variant="outline" onClick={handleChangeMethod}>
                Try again
              </Button>
              <Button type="button" onClick={() => close(false)}>
                Close
              </Button>
            </>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
