import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
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
import type { AddClaudeAccountRequest } from "@/features/claude/schemas";

const DEFAULT_EXPIRES_IN_SECONDS = 3600;

type FormState = {
  claudeAccountUuid: string;
  accessToken: string;
  refreshToken: string;
  expiresInSeconds: string;
  scopes: string;
  userEmail: string;
  userOrganizationUuid: string;
};

const INITIAL_FORM: FormState = {
  claudeAccountUuid: "",
  accessToken: "",
  refreshToken: "",
  expiresInSeconds: String(DEFAULT_EXPIRES_IN_SECONDS),
  scopes: "",
  userEmail: "",
  userOrganizationUuid: "",
};

type FieldErrors = Partial<Record<keyof FormState, string>>;

function parseScopes(value: string): string[] | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  return trimmed
    .split(",")
    .map((scope) => scope.trim())
    .filter((scope) => scope.length > 0);
}

function buildPayload(form: FormState): AddClaudeAccountRequest {
  const expiresInSeconds = Number.parseInt(form.expiresInSeconds, 10);
  const scopes = parseScopes(form.scopes);
  const payload: AddClaudeAccountRequest = {
    claudeAccountUuid: form.claudeAccountUuid.trim(),
    accessToken: form.accessToken.trim(),
    refreshToken: form.refreshToken.trim(),
    expiresInSeconds,
    ...(scopes ? { scopes } : {}),
  };
  const userEmail = form.userEmail.trim();
  if (userEmail) {
    payload.userEmail = userEmail;
  }
  const userOrganizationUuid = form.userOrganizationUuid.trim();
  if (userOrganizationUuid) {
    payload.userOrganizationUuid = userOrganizationUuid;
  }
  return payload;
}

function validate(form: FormState, requiredLabel: string, positiveIntLabel: string): FieldErrors {
  const errors: FieldErrors = {};
  if (!form.claudeAccountUuid.trim()) {
    errors.claudeAccountUuid = requiredLabel;
  }
  if (!form.accessToken.trim()) {
    errors.accessToken = requiredLabel;
  }
  if (!form.refreshToken.trim()) {
    errors.refreshToken = requiredLabel;
  }
  const expires = Number.parseInt(form.expiresInSeconds, 10);
  if (!Number.isFinite(expires) || expires <= 0) {
    errors.expiresInSeconds = positiveIntLabel;
  }
  return errors;
}

export type AddClaudeAccountDialogProps = {
  open: boolean;
  busy: boolean;
  errorMessage?: string | null;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: AddClaudeAccountRequest) => Promise<void> | void;
};

export function AddClaudeAccountDialog({
  open,
  busy,
  errorMessage,
  onOpenChange,
  onSubmit,
}: AddClaudeAccountDialogProps) {
  const { t } = useTranslation();
  const [form, setForm] = useState<FormState>(INITIAL_FORM);
  const [errors, setErrors] = useState<FieldErrors>({});

  const requiredLabel = t("claude.addDialog.validation.required");
  const positiveIntLabel = t("claude.addDialog.validation.positiveInteger");

  const updateField = (field: keyof FormState) =>
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const value = event.target.value;
      setForm((prev) => ({ ...prev, [field]: value }));
      setErrors((prev) => {
        if (!prev[field]) return prev;
        const next = { ...prev };
        delete next[field];
        return next;
      });
    };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const nextErrors = validate(form, requiredLabel, positiveIntLabel);
    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors);
      return;
    }
    try {
      await onSubmit(buildPayload(form));
      setForm(INITIAL_FORM);
      setErrors({});
    } catch {
      // Parent displays the failure via errorMessage; keep form state.
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("claude.addDialog.title")}</DialogTitle>
            <DialogDescription>{t("claude.addDialog.description")}</DialogDescription>
          </DialogHeader>

          <form onSubmit={handleSubmit} className="space-y-3" data-testid="add-claude-account-form">
            {errorMessage ? <AlertMessage variant="error">{errorMessage}</AlertMessage> : null}

            <div className="space-y-1">
              <Label htmlFor="add-claude-account-uuid">
                {t("claude.addDialog.fields.claudeAccountUuid")}
              </Label>
              <Input
                id="add-claude-account-uuid"
                value={form.claudeAccountUuid}
                onChange={updateField("claudeAccountUuid")}
                placeholder={t("claude.addDialog.fields.claudeAccountUuidPlaceholder")}
                autoComplete="off"
                aria-invalid={errors.claudeAccountUuid ? true : undefined}
              />
              {errors.claudeAccountUuid ? (
                <p className="text-xs text-destructive">{errors.claudeAccountUuid}</p>
              ) : null}
            </div>

            <div className="space-y-1">
              <Label htmlFor="add-claude-account-access-token">
                {t("claude.addDialog.fields.accessToken")}
              </Label>
              <Input
                id="add-claude-account-access-token"
                type="password"
                value={form.accessToken}
                onChange={updateField("accessToken")}
                autoComplete="off"
                aria-invalid={errors.accessToken ? true : undefined}
              />
              {errors.accessToken ? (
                <p className="text-xs text-destructive">{errors.accessToken}</p>
              ) : null}
            </div>

            <div className="space-y-1">
              <Label htmlFor="add-claude-account-refresh-token">
                {t("claude.addDialog.fields.refreshToken")}
              </Label>
              <Input
                id="add-claude-account-refresh-token"
                type="password"
                value={form.refreshToken}
                onChange={updateField("refreshToken")}
                autoComplete="off"
                aria-invalid={errors.refreshToken ? true : undefined}
              />
              {errors.refreshToken ? (
                <p className="text-xs text-destructive">{errors.refreshToken}</p>
              ) : null}
            </div>

            <div className="space-y-1">
              <Label htmlFor="add-claude-account-expires-in">
                {t("claude.addDialog.fields.expiresInSeconds")}
              </Label>
              <Input
                id="add-claude-account-expires-in"
                type="number"
                inputMode="numeric"
                min={1}
                value={form.expiresInSeconds}
                onChange={updateField("expiresInSeconds")}
                aria-invalid={errors.expiresInSeconds ? true : undefined}
              />
              {errors.expiresInSeconds ? (
                <p className="text-xs text-destructive">{errors.expiresInSeconds}</p>
              ) : null}
            </div>

            <div className="space-y-1">
              <Label htmlFor="add-claude-account-scopes">
                {t("claude.addDialog.fields.scopes")}
              </Label>
              <Input
                id="add-claude-account-scopes"
                value={form.scopes}
                onChange={updateField("scopes")}
                placeholder={t("claude.addDialog.fields.scopesPlaceholder")}
                autoComplete="off"
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="add-claude-account-user-email">
                {t("claude.addDialog.fields.userEmail")}
              </Label>
              <Input
                id="add-claude-account-user-email"
                type="email"
                value={form.userEmail}
                onChange={updateField("userEmail")}
                autoComplete="off"
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="add-claude-account-user-org-uuid">
                {t("claude.addDialog.fields.userOrganizationUuid")}
              </Label>
              <Input
                id="add-claude-account-user-org-uuid"
                value={form.userOrganizationUuid}
                onChange={updateField("userOrganizationUuid")}
                autoComplete="off"
              />
            </div>

            <DialogFooter className="pt-2">
              <Button type="submit" disabled={busy}>
                {t("claude.addDialog.submit")}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}