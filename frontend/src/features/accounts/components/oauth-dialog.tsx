import { ExternalLink } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { OAuthState } from "@/features/accounts/schemas";
import { formatCountdown } from "@/utils/formatters";

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
  const close = (next: boolean) => {
    onOpenChange(next);
    if (!next) {
      onReset();
    }
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add account with OAuth</DialogTitle>
          <DialogDescription>Choose browser or device flow and complete authorization.</DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" onClick={() => void onStart("browser")}>Start Browser Flow</Button>
            <Button type="button" size="sm" variant="outline" onClick={() => void onStart("device")}>
              Start Device Flow
            </Button>
          </div>

          {state.method ? (
            <div className="space-y-1 rounded-lg border p-3 text-xs">
              <p>
                <span className="font-medium">Method:</span> {state.method}
              </p>
              <p>
                <span className="font-medium">Status:</span> {state.status}
              </p>
              {state.expiresInSeconds ? (
                <p>
                  <span className="font-medium">Expires in:</span> {formatCountdown(state.expiresInSeconds)}
                </p>
              ) : null}
              {state.userCode ? (
                <p>
                  <span className="font-medium">User code:</span> {state.userCode}
                </p>
              ) : null}
              {state.authorizationUrl ? (
                <a
                  href={state.authorizationUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-primary underline"
                >
                  Open authorization URL
                  <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
              {state.verificationUrl ? (
                <a
                  href={state.verificationUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-primary underline"
                >
                  Open device verification URL
                  <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
            </div>
          ) : null}

          {state.errorMessage ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 text-xs text-destructive">
              {state.errorMessage}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => void onComplete()}>
            Complete OAuth
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
