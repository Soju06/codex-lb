import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { CopyButton } from "@/components/copy-button";
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
  startClaudeOauth,
  submitClaudeOauthCallback,
} from "@/features/claude/api";
import type {
  ClaudeOauthCallbackRequest,
  ClaudeOauthStartResponse,
} from "@/features/claude/schemas";

type Step = "idle" | "started" | "submitting" | "success" | "error";

type ErrorState = {
  message: string;
  code: string | null;
};

export type AddClaudeAccountOAuthDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess: () => void;
};

function extractErrorCode(error: unknown): string | null {
  if (error && typeof error === "object" && "code" in error) {
    const candidate = (error as { code: unknown }).code;
    if (typeof candidate === "string" && candidate.length > 0) {
      return candidate;
    }
  }
  return null;
}

function extractErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  if (error && typeof error === "object" && "message" in error) {
    const candidate = (error as { message: unknown }).message;
    if (typeof candidate === "string" && candidate.length > 0) {
      return candidate;
    }
  }
  return fallback;
}

type DialogBodyProps = {
  onSuccess: () => void;
  onClose: () => void;
};

function DialogBody({ onSuccess, onClose }: DialogBodyProps) {
  const { t } = useTranslation();
  const [step, setStep] = useState<Step>("idle");
  const [startData, setStartData] = useState<ClaudeOauthStartResponse | null>(null);
  const [code, setCode] = useState("");
  const [errorState, setErrorState] = useState<ErrorState | null>(null);

  const handleStart = async () => {
    setErrorState(null);
    setCode("");
    setStep("idle");
    try {
      const data = await startClaudeOauth();
      setStartData(data);
      setStep("started");
    } catch (error) {
      setErrorState({
        message: extractErrorMessage(error, t("claude.oauth.error.generic")),
        code: extractErrorCode(error),
      });
      setStep("error");
    }
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!startData) return;
    const trimmedCode = code.trim();
    if (!trimmedCode) return;
    setStep("submitting");
    setErrorState(null);
    const payload: ClaudeOauthCallbackRequest = {
      flowId: startData.flowId,
      code: trimmedCode,
      state: startData.stateToken,
    };
    try {
      await submitClaudeOauthCallback(payload);
      setStep("success");
      onSuccess();
      onClose();
    } catch (error) {
      setErrorState({
        message: extractErrorMessage(error, t("claude.oauth.error.generic")),
        code: extractErrorCode(error),
      });
      setStep("error");
    }
  };

  const errorMessage = errorState
    ? t(`claude.oauth.error.${errorState.code ?? "generic"}`, {
        defaultValue: errorState.message,
      })
    : null;

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t("claude.oauth.add.button")}</DialogTitle>
        <DialogDescription>
          {startData?.callbackInstructions ?? t("claude.oauth.step1.title")}
        </DialogDescription>
      </DialogHeader>

      {step === "idle" ? (
        <div className="space-y-3" data-testid="add-claude-account-oauth-idle">
          {errorMessage ? (
            <AlertMessage variant="error">{errorMessage}</AlertMessage>
          ) : null}
          <DialogFooter className="pt-2">
            <Button
              type="button"
              onClick={() => {
                void handleStart();
              }}
              data-testid="add-claude-account-oauth-start"
            >
              {t("claude.oauth.add.button")}
            </Button>
          </DialogFooter>
        </div>
      ) : null}

      {step === "started" || step === "submitting" ? (
        <form
          onSubmit={handleSubmit}
          className="space-y-3"
          data-testid="add-claude-account-oauth-form"
        >
          {errorMessage ? (
            <AlertMessage variant="error">{errorMessage}</AlertMessage>
          ) : null}

          <div className="space-y-1">
            <Label htmlFor="add-claude-account-oauth-url">
              {t("claude.oauth.step1.title")}
            </Label>
            <div className="flex gap-2">
              <Input
                id="add-claude-account-oauth-url"
                readOnly
                value={startData?.authorizationUrl ?? ""}
                autoComplete="off"
                className="font-mono text-xs"
              />
              <CopyButton
                value={startData?.authorizationUrl ?? ""}
                label={t("claude.oauth.step1.copy")}
                data-testid="add-claude-account-oauth-copy"
              />
            </div>
            {startData?.authorizationUrl ? (
              <a
                href={startData.authorizationUrl}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-primary underline-offset-4 hover:underline"
              >
                {t("claude.oauth.step1.open")}
              </a>
            ) : null}
          </div>

          <div className="space-y-1">
            <Label htmlFor="add-claude-account-oauth-code">
              {t("claude.oauth.step2.codeLabel")}
            </Label>
            <textarea
              id="add-claude-account-oauth-code"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              placeholder={t("claude.oauth.step2.codePlaceholder")}
              autoFocus
              autoComplete="off"
              spellCheck={false}
              rows={3}
              className="border-input bg-transparent ring-offset-background placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 dark:bg-input/30 flex w-full min-w-0 rounded-md border px-3 py-1.5 text-base shadow-xs transition-[color,box-shadow] outline-none disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm"
            />
          </div>

          <div className="space-y-1">
            <Label htmlFor="add-claude-account-oauth-state">
              {t("claude.oauth.step2.stateLabel")}
            </Label>
            <Input
              id="add-claude-account-oauth-state"
              readOnly
              value={startData?.stateToken ?? ""}
              autoComplete="off"
              className="font-mono text-xs"
            />
          </div>

          <DialogFooter className="pt-2">
            <Button
              type="submit"
              disabled={step === "submitting" || code.trim().length === 0}
              data-testid="add-claude-account-oauth-submit"
            >
              {t("claude.oauth.step2.submit")}
            </Button>
          </DialogFooter>
        </form>
      ) : null}

      {step === "success" ? (
        <div className="space-y-3" data-testid="add-claude-account-oauth-success">
          <AlertMessage variant="success">{t("claude.oauth.step3.title")}</AlertMessage>
        </div>
      ) : null}

      {step === "error" ? (
        <div className="space-y-3" data-testid="add-claude-account-oauth-error">
          <AlertMessage variant="error">
            {errorMessage ?? t("claude.oauth.error.generic")}
          </AlertMessage>
          <DialogFooter className="pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                void handleStart();
              }}
              data-testid="add-claude-account-oauth-start-over"
            >
              {t("claude.oauth.error.startOver")}
            </Button>
          </DialogFooter>
        </div>
      ) : null}
    </>
  );
}

export function AddClaudeAccountOAuthDialog({
  open,
  onOpenChange,
  onSuccess,
}: AddClaudeAccountOAuthDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent className="sm:max-w-lg">
          <DialogBody
            onSuccess={onSuccess}
            onClose={() => onOpenChange(false)}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
