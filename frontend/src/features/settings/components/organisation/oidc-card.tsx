import { Building2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  flowNavigation,
  openFlowWindow,
  rememberFlow,
  takeFlow,
  watchFlowWindow,
} from "@/features/auth/oidc-window";
import type { AuthProvider } from "@/features/organisation/api";
import { organisationErrorMessage, useOrganisationMutations } from "@/features/organisation/hooks";
import { armedByTestLogin, isConnected } from "@/features/organisation/rules";
import { ApiError } from "@/lib/api-client";
import { ORGANISATION_OIDC_ID } from "@/features/settings/advanced-settings-deeplink";
import {
  OidcConnectDialog,
  type OidcStep,
} from "@/features/settings/components/organisation/oidc-connect-dialog";

export type OidcCardProps = {
  provider: AuthProvider;
  mutations: ReturnType<typeof useOrganisationMutations>;
  disabled?: boolean;
};

/**
 * Connecting the identity provider the company already uses. The card is the
 * whole surface: the wizard is a dialog inside it, and an install that never
 * connects anything meets one invitation and nothing else.
 *
 * Turning the connection on needs a test sign-in this card watched complete —
 * not merely a stamp on the row. The server binds the proof to the account that
 * earned it and refuses anybody else's with a `409`, and it will not say whose
 * a stored proof is, so arming from the timestamp alone would be offering a
 * control the server means to refuse.
 */
export function OidcCard({ provider, mutations, disabled = false }: OidcCardProps) {
  const { t } = useTranslation();
  // A flow the browser would not give a window to finished in this tab, so the
  // page was reloaded and the provider row is being read fresh anyway. The
  // marker is consumed once, at mount, and only ever opens the card back up.
  const [marker] = useState(takeFlow);
  const arrival = marker !== null && marker.providerId === provider.id ? marker : null;
  const [dialogAt, setDialogAt] = useState<OidcStep | null>(arrival === null ? null : "test");
  // A pre-flight this card started, and the stamp the row carried before it.
  // In memory only: cleared by a connection write, by a successful enable and
  // by a reload, because none of those leave a proof this browser may spend.
  const [flow, setFlow] = useState<{ observed: string | null } | null>(
    arrival === null ? null : { observed: arrival.verifiedAt },
  );
  const [returned, setReturned] = useState(arrival !== null);
  const [startRefusal, setStartRefusal] = useState<unknown>(null);
  const [enableRefusal, setEnableRefusal] = useState<unknown>(null);
  const stopWatching = useRef<(() => void) | null>(null);

  const busy = disabled || mutations.busy;
  const connected = isConnected(provider);
  const armed = flow !== null && armedByTestLogin(flow.observed, provider.testLoginVerifiedAt);

  useEffect(() => () => stopWatching.current?.(), []);

  const runTestLogin = () => {
    // Synchronously, before the start is awaited: the start is itself step-up
    // gated and can interpose a dialog, and a window opened after an await is
    // blocked by every browser.
    const handle = openFlowWindow();
    const observed = provider.testLoginVerifiedAt;
    setStartRefusal(null);
    setEnableRefusal(null);
    setReturned(false);
    setFlow({ observed });
    void mutations.startTestLogin
      .mutateAsync()
      .then(({ authorizationUrl }) => {
        if (handle === null) {
          rememberFlow({ providerId: provider.id, purpose: "test-login", verifiedAt: observed });
          flowNavigation.go(authorizationUrl);
          return;
        }
        handle.location.href = authorizationUrl;
        stopWatching.current?.();
        stopWatching.current = watchFlowWindow(handle, (reason) => {
          // A handle reporting itself closed is not a round trip that ended:
          // an identity provider serving a cross-origin opener policy severs
          // the handle while the person is still signing in, and a severed one
          // reports exactly that. Re-read on it, but say nothing — the failure
          // sentence belongs to a window that actually came back, or to a flow
          // whose ten minutes are up.
          setReturned(reason !== "closed");
          void mutations.refreshProviders();
        });
      })
      .catch((caught: unknown) => {
        handle?.close();
        setFlow(null);
        setStartRefusal(caught);
      });
  };

  const write = (payload: Parameters<typeof mutations.updateOidcProvider.mutateAsync>[0]["payload"]) =>
    mutations.updateOidcProvider.mutateAsync({ providerId: provider.id, payload });

  const turnOn = () => {
    setEnableRefusal(null);
    void write({ enabled: true })
      .then(() => {
        // The server spends the proof on the enable: off and on again needs a new one.
        setFlow(null);
        setReturned(false);
        setDialogAt(null);
      })
      .catch(setEnableRefusal);
  };

  // The rate limit is the one refusal whose explanation needs a number, and the
  // number rides in `Retry-After` rather than the envelope. The budget it spends
  // is shared with the public sign-in start, so nothing here retries on its own.
  const startMessage = () =>
    startRefusal instanceof ApiError &&
    startRefusal.code === "oidc_rate_limited" &&
    startRefusal.retryAfter !== null
      ? t("organisation.oidc.rateLimited", { seconds: startRefusal.retryAfter })
      : organisationErrorMessage(startRefusal, t);

  const testPanel = (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">{t("organisation.oidc.test.help")}</p>
      {startRefusal !== null ? <AlertMessage variant="error">{startMessage()}</AlertMessage> : null}
      {enableRefusal !== null ? (
        <AlertMessage variant="error">{organisationErrorMessage(enableRefusal, t)}</AlertMessage>
      ) : null}
      {armed ? (
        <AlertMessage variant="success">{t("organisation.oidc.test.armed")}</AlertMessage>
      ) : returned ? (
        <AlertMessage variant="warning">{t("organisation.oidc.test.incomplete")}</AlertMessage>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" disabled={busy} onClick={runTestLogin}>
          {t("organisation.oidc.actions.runTest")}
        </Button>
        <Button type="button" size="sm" disabled={busy || !armed} onClick={turnOn}>
          {t("organisation.oidc.actions.turnOn")}
        </Button>
      </div>
      {armed ? null : <p className="text-[11px] text-muted-foreground">{t("organisation.oidc.test.gate")}</p>}
    </div>
  );

  return (
    <section id={ORGANISATION_OIDC_ID} className="scroll-mt-16 space-y-3 rounded-xl border bg-card p-5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <Building2 className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">{t("organisation.oidc.title")}</h3>
          <p className="text-xs text-muted-foreground">{t("organisation.oidc.description")}</p>
        </div>
      </div>

      {!connected ? (
        <div className="space-y-2" data-testid="oidc-empty">
          <p className="text-xs text-muted-foreground">{t("organisation.oidc.empty")}</p>
          <Button type="button" size="sm" disabled={busy} onClick={() => setDialogAt("connection")}>
            {t("organisation.oidc.actions.connect")}
          </Button>
        </div>
      ) : (
        <div className="space-y-3" data-testid="oidc-connected">
          <div className="grid gap-2 rounded-lg border p-3 sm:grid-cols-2">
            <div className="space-y-0.5">
              <p className="text-xs text-muted-foreground">{t("organisation.oidc.fields.label")}</p>
              <p className="text-sm font-medium">{provider.label}</p>
            </div>
            <div className="space-y-0.5">
              <p className="text-xs text-muted-foreground">{t("organisation.oidc.fields.issuer")}</p>
              <p className="font-mono text-sm font-medium break-all">{provider.config["issuer"] ?? "-"}</p>
            </div>
          </div>

          {provider.enabled ? (
            <div className="flex items-start justify-between gap-3 rounded-lg border p-3">
              <div>
                <p className="text-sm font-medium">{t("organisation.oidc.onLabel")}</p>
                <p className="text-xs text-muted-foreground">
                  {t("organisation.oidc.onDescription", { label: provider.label })}
                </p>
              </div>
              {/* Turning a sign-in method off is never gated, so this control
                  stays available even when a widening write was refused. */}
              <Switch
                aria-label={t("organisation.oidc.onLabel")}
                checked
                disabled={busy}
                onCheckedChange={() => void write({ enabled: false }).catch(setEnableRefusal)}
              />
            </div>
          ) : (
            <AlertMessage variant="warning">{t("organisation.oidc.savedNotOn")}</AlertMessage>
          )}

          {enableRefusal !== null && dialogAt === null ? (
            <AlertMessage variant="error">{organisationErrorMessage(enableRefusal, t)}</AlertMessage>
          ) : null}

          <div className="flex flex-wrap gap-2">
            {provider.enabled ? null : (
              <Button type="button" size="sm" disabled={busy} onClick={() => setDialogAt("test")}>
                {t("organisation.oidc.actions.testAndTurnOn")}
              </Button>
            )}
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => setDialogAt("connection")}
            >
              {t("organisation.oidc.actions.edit")}
            </Button>
          </div>
        </div>
      )}

      {dialogAt === null ? null : (
        <OidcConnectDialog
          provider={provider}
          startAt={dialogAt}
          mutations={mutations}
          onClose={() => setDialogAt(null)}
          onConnectionSaved={() => {
            // The server clears the proof when the connection changes, so the
            // control follows it down rather than remembering a dead success.
            setFlow(null);
            setReturned(false);
            setEnableRefusal(null);
          }}
        >
          {testPanel}
        </OidcConnectDialog>
      )}
    </section>
  );
}
