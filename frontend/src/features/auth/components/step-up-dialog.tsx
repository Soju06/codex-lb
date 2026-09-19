import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

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
import { InputOTP, InputOTPGroup, InputOTPSeparator, InputOTPSlot } from "@/components/ui/input-otp";
import { Label } from "@/components/ui/label";
import { startOidcStepUp, stepUp } from "@/features/auth/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { ACCESS_HASH } from "@/features/settings/advanced-settings-deeplink";
import { setStepUpHandlers, type StepUpMethod } from "@/lib/api-client";
import { getErrorMessage } from "@/utils/errors";

type PendingStepUp = {
  methods: StepUpMethod[];
  resolve: (verified: boolean) => void;
};

/**
 * Asks the signed-in person to confirm a sensitive change by re-entering the
 * factors their account holds (password and/or authenticator code). Mounted
 * once; the API client opens it on `403 step_up_required` and replays the
 * interrupted request when it succeeds, so callers never see the interruption.
 *
 * An account that holds neither is offered the one factor it does have: a fresh
 * authentication at its identity provider. That leg cannot be a form post — it
 * is a round trip through the provider — so it replaces the inputs with a
 * button and leaves the page. The interrupted request is settled as "gave up"
 * before the navigation, because nothing can replay it across a page load; the
 * person repeats the action once the callback has brought them back.
 */
export function StepUpDialog() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const refreshSession = useAuthStore((state) => state.refreshSession);
  const [pending, setPending] = useState<PendingStepUp | null>(null);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Several requests may hit the gate at once (a form that saves in parallel):
  // they all wait on the same dialog and share its answer.
  const waiters = useRef<Array<(verified: boolean) => void>>([]);

  const settle = useCallback((verified: boolean) => {
    const resolvers = waiters.current;
    waiters.current = [];
    for (const resolve of resolvers) resolve(verified);
    setPending(null);
    setPassword("");
    setCode("");
    setError(null);
  }, []);

  useEffect(() => {
    setStepUpHandlers({
      onRequired: (methods) =>
        new Promise<boolean>((resolve) => {
          waiters.current.push(resolve);
          setPending((current) => current ?? { methods, resolve });
        }),
      onUnavailable: () => {
        toast.error(t("auth.stepUp.unavailable.title"), {
          description: t("auth.stepUp.unavailable.description"),
          action: {
            label: t("auth.stepUp.unavailable.action"),
            onClick: () => navigate(`/settings${ACCESS_HASH}`),
          },
        });
      },
    });
    return () => setStepUpHandlers(null);
  }, [navigate, t]);

  const needsPassword = pending?.methods.includes("password") ?? false;
  const needsCode = pending?.methods.includes("totp") ?? false;
  // The identity provider is never asked for alongside a factor the account
  // holds: the server offers it only to an account that holds none, precisely
  // so a live provider session cannot stand in for something an attacker would
  // otherwise have to produce. Mirrored here rather than assumed, so a payload
  // that ever mixed them would still ask for the stronger factors.
  const needsProvider = !needsPassword && !needsCode && (pending?.methods.includes("oidc") ?? false);
  const complete = (!needsPassword || password.length > 0) && (!needsCode || code.length === 6);

  const continueAtProvider = async () => {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      const { authorizationUrl } = await startOidcStepUp();
      settle(false);
      window.location.assign(authorizationUrl);
    } catch (caught) {
      setError(getErrorMessage(caught));
      setBusy(false);
    }
  };

  const submit = async () => {
    // `needsProvider` first: with neither input rendered, `complete` is
    // vacuously true, so Enter inside the form would post the empty `/step-up`
    // body the server refuses — the dead end this branch exists to remove.
    if (!pending || needsProvider || !complete) return;
    setBusy(true);
    setError(null);
    try {
      await stepUp({
        ...(needsPassword ? { password } : {}),
        ...(needsCode ? { code } : {}),
      });
      await refreshSession().catch(() => undefined);
      settle(true);
    } catch (caught) {
      setError(getErrorMessage(caught));
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={pending !== null} onOpenChange={(open) => !open && settle(false)}>
      <DialogContent className="sm:max-w-sm" onInteractOutside={(event) => event.preventDefault()}>
        <DialogHeader>
          <DialogTitle>{t("auth.stepUp.title")}</DialogTitle>
          <DialogDescription>
            {needsProvider ? t("auth.stepUp.provider.description") : t("auth.stepUp.description")}
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          {needsPassword ? (
            <div className="space-y-2">
              <Label htmlFor="step-up-password">{t("auth.stepUp.passwordLabel")}</Label>
              <Input
                id="step-up-password"
                type="password"
                autoComplete="current-password"
                autoFocus
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={busy}
              />
            </div>
          ) : null}
          {needsCode ? (
            <div className="flex flex-col items-center gap-2">
              <Label htmlFor="step-up-code">{t("auth.stepUp.codeLabel")}</Label>
              <InputOTP
                id="step-up-code"
                maxLength={6}
                autoFocus={!needsPassword}
                value={code}
                onChange={setCode}
                disabled={busy}
              >
                <InputOTPGroup>
                  <InputOTPSlot index={0} />
                  <InputOTPSlot index={1} />
                  <InputOTPSlot index={2} />
                </InputOTPGroup>
                <InputOTPSeparator />
                <InputOTPGroup>
                  <InputOTPSlot index={3} />
                  <InputOTPSlot index={4} />
                  <InputOTPSlot index={5} />
                </InputOTPGroup>
              </InputOTP>
            </div>
          ) : null}
          {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => settle(false)} disabled={busy}>
              {t("common.cancel")}
            </Button>
            {needsProvider ? (
              <Button type="button" disabled={busy} onClick={() => void continueAtProvider()}>
                {t("auth.stepUp.provider.submit")}
              </Button>
            ) : (
              <Button type="submit" disabled={busy || !complete}>
                {t("auth.stepUp.submit")}
              </Button>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
