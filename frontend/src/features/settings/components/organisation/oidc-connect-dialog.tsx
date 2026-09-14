import { useState, type ReactNode } from "react";
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
import { OIDC_CALLBACK_PATH, type AuthProvider, type OidcConfigRequest } from "@/features/organisation/api";
import { organisationErrorMessage, refusedOidcField, useOrganisationMutations } from "@/features/organisation/hooks";
import {
  OIDC_CLAIM_FIELDS,
  OIDC_CONNECTION_FIELDS,
  validateOidcDraft,
  type OidcDraft,
  type OidcField,
  type OidcProblem,
} from "@/features/organisation/rules";

/** Which of the three steps the dialog is on. The order is the plan's. */
export type OidcStep = "connection" | "claims" | "test";

const STEPS: OidcStep[] = ["connection", "claims", "test"];

/** What each claim name falls back to when the operator leaves it blank. */
const CLAIM_DEFAULTS: Record<(typeof OIDC_CLAIM_FIELDS)[number], string> = {
  subjectClaim: "sub",
  emailClaim: "email",
  nameClaim: "name",
  groupsClaim: "groups",
};

const EMPTY_DRAFT: OidcDraft = {
  issuer: "",
  discoveryUrl: "",
  clientId: "",
  clientSecret: "",
  redirectUri: "",
  subjectClaim: "",
  emailClaim: "",
  nameClaim: "",
  groupsClaim: "",
};

/**
 * The stored connection as a form draft. Everything the API returns in clear is
 * pre-filled so an edit does not start from nothing — except the secret, which
 * comes back masked and has to be typed again: the server replaces the whole
 * document on every write and will not inherit a secret behind a repointed
 * issuer, so sending the mask back would be sending a credential nobody holds.
 */
function draftFrom(provider: AuthProvider): OidcDraft {
  const stored = provider.config;
  const draft = { ...EMPTY_DRAFT };
  for (const name of [...OIDC_CONNECTION_FIELDS, ...OIDC_CLAIM_FIELDS]) {
    if (name !== "clientSecret") {
      draft[name] = stored[name] ?? "";
    }
  }
  if (draft.redirectUri === "" && typeof window !== "undefined") {
    draft.redirectUri = `${window.location.origin}${OIDC_CALLBACK_PATH}`;
  }
  return draft;
}

/** The document as the API wants it; a blank optional field means "use the default". */
function configFrom(draft: OidcDraft): OidcConfigRequest {
  const optional = (value: string) => (value.trim() === "" ? null : value.trim());
  return {
    issuer: draft.issuer.trim(),
    discoveryUrl: optional(draft.discoveryUrl),
    clientId: draft.clientId.trim(),
    clientSecret: draft.clientSecret,
    redirectUri: draft.redirectUri.trim(),
    subjectClaim: optional(draft.subjectClaim),
    emailClaim: optional(draft.emailClaim),
    nameClaim: optional(draft.nameClaim),
    groupsClaim: optional(draft.groupsClaim),
  };
}

export type OidcConnectDialogProps = {
  provider: AuthProvider;
  /** Where to land: the connection when editing, the test login when resuming. */
  startAt: OidcStep;
  onClose: () => void;
  /** The connection changed, so whatever proof the card was holding is gone. */
  onConnectionSaved: () => void;
  mutations: ReturnType<typeof useOrganisationMutations>;
  /** Step (c): the card owns the round trip, so it renders that panel. */
  children: ReactNode;
};

/**
 * The connect wizard: three steps inside the card, in the plan's order —
 * connection, claim names, test sign-in. It is a dialog and not a first-run
 * flow, so an install that never connects an identity provider never meets it.
 *
 * Steps (a) and (b) are one write. The server refuses a partial `config` and
 * the pre-flight runs against the row as stored, so there is no half-configured
 * state to design for: the operator either wrote nothing, or wrote a connection
 * on a row that is still off and admits nobody.
 *
 * The dialog is mounted only while it is open, so the draft — and the client
 * secret in it — dies with the interaction that needed it. The draft is not the
 * only copy, though: the write's variables are the *group's* mutation state and
 * would outlive this dialog, so `save` hands that mutation back once the round
 * trip is over. Nothing else here reaches a surface that keeps anything. The
 * secret is never in the URL, never in storage, never in history, and cannot be
 * in a refusal: `ApiError` carries the server's envelope and response body, and
 * no part of the request that provoked it.
 */
export function OidcConnectDialog({
  provider,
  startAt,
  onClose,
  onConnectionSaved,
  mutations,
  children,
}: OidcConnectDialogProps) {
  const { t } = useTranslation();
  const [step, setStep] = useState<OidcStep>(startAt);
  const [draft, setDraft] = useState<OidcDraft>(() => draftFrom(provider));
  const [problems, setProblems] = useState<Partial<Record<OidcField, OidcProblem>>>({});
  // This visit's refusal, not the shared mutation's: the provider mutation is
  // the whole group's, and an error the reverse-proxy card provoked is not
  // something this dialog may present as its own.
  const [refusal, setRefusal] = useState<unknown>(null);

  const refusedField = refusedOidcField(refusal);
  const error = refusal === null ? null : organisationErrorMessage(refusal, t);
  const busy = mutations.busy;
  const storedSecret = provider.config["clientSecret"] ?? null;

  const save = async () => {
    const found = validateOidcDraft(draft, { callbackPath: OIDC_CALLBACK_PATH });
    setProblems(found);
    if (Object.keys(found).length > 0) {
      // Step (a) owns most of the rules, so land wherever the first one is fixable.
      setStep(OIDC_CONNECTION_FIELDS.some((name) => found[name]) ? "connection" : "claims");
      return;
    }
    try {
      await mutations.updateOidcProvider.mutateAsync({
        providerId: provider.id,
        payload: { config: configFrom(draft) },
      });
      setRefusal(null);
      onConnectionSaved();
      setStep("test");
    } catch (caught) {
      setRefusal(caught);
      // A field-named refusal belongs on its field, and on the step showing it.
      const named = refusedOidcField(caught);
      if (named) {
        setStep(OIDC_CONNECTION_FIELDS.some((name) => name === named) ? "connection" : "claims");
      }
    } finally {
      // The document just sent is the mutation's `variables`, and a settled
      // mutation keeps them. That state belongs to the group's hook, not to
      // this dialog, so the client secret in it would outlive every surface
      // with a reason to hold it. Handing the mutation back drops it — on the
      // refused path too, since this dialog keeps its own copy of the refusal
      // and reads nothing else off the mutation but `isPending`.
      mutations.updateOidcProvider.reset();
    }
  };

  const field = (name: OidcField, { optional = false }: { optional?: boolean } = {}) => {
    const problem = problems[name];
    const refused = refusedField === name;
    return (
      <div className="space-y-1">
        <Label htmlFor={`oidc-${name}`} className="text-xs font-medium">
          {t(`organisation.oidc.fields.${name}`)}
          {optional ? <span className="text-muted-foreground">{` ${t("organisation.oidc.optional")}`}</span> : null}
        </Label>
        <div className="flex items-center gap-2">
          <Input
            id={`oidc-${name}`}
            value={draft[name]}
            type={name === "clientSecret" ? "password" : "text"}
            autoComplete={name === "clientSecret" ? "new-password" : "off"}
            aria-invalid={problem !== undefined || refused}
            disabled={busy}
            onChange={(event) => setDraft({ ...draft, [name]: event.target.value })}
          />
          {name === "redirectUri" ? (
            <CopyButton value={draft.redirectUri} label={t("organisation.oidc.copyRedirect")} />
          ) : null}
        </div>
        {problem ? (
          <p className="text-[11px] font-medium text-destructive">
            {t(`organisation.oidc.validation.${problem}`, { path: OIDC_CALLBACK_PATH })}
          </p>
        ) : refused ? (
          <p className="text-[11px] font-medium text-destructive">{t("organisation.oidc.fieldRefused")}</p>
        ) : (
          <p className="text-[11px] text-muted-foreground">{t(`organisation.oidc.help.${name}`)}</p>
        )}
      </div>
    );
  };

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("organisation.oidc.dialog.title")}</DialogTitle>
          <DialogDescription>
            {t("organisation.oidc.dialog.stepOf", {
              current: STEPS.indexOf(step) + 1,
              total: STEPS.length,
              name: t(`organisation.oidc.steps.${step}`),
            })}
          </DialogDescription>
        </DialogHeader>

        {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

        {step === "connection" ? (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">{t("organisation.oidc.dialog.connectionHelp")}</p>
            {field("issuer")}
            {field("discoveryUrl", { optional: true })}
            {field("clientId")}
            {field("clientSecret")}
            {storedSecret ? (
              <p className="text-[11px] text-muted-foreground" data-testid="oidc-stored-secret">
                {t("organisation.oidc.secretStored", { masked: storedSecret })}
              </p>
            ) : null}
            {field("redirectUri")}
          </div>
        ) : null}

        {step === "claims" ? (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">{t("organisation.oidc.dialog.claimsHelp")}</p>
            {OIDC_CLAIM_FIELDS.map((name) => (
              <div key={name} className="space-y-1">
                {field(name, { optional: true })}
                <p className="text-[11px] text-muted-foreground">
                  {t("organisation.oidc.claimDefault", { name: CLAIM_DEFAULTS[name] })}
                </p>
              </div>
            ))}
          </div>
        ) : null}

        {step === "test" ? children : null}

        <DialogFooter className="sm:justify-between">
          <Button
            type="button"
            variant="ghost"
            disabled={busy || step === "connection"}
            onClick={() => setStep(step === "test" ? "claims" : "connection")}
          >
            {t("organisation.oidc.actions.back")}
          </Button>
          {step === "connection" ? (
            <Button type="button" disabled={busy} onClick={() => setStep("claims")}>
              {t("organisation.oidc.actions.next")}
            </Button>
          ) : step === "claims" ? (
            <Button type="button" disabled={busy} onClick={() => void save()}>
              {t("organisation.oidc.actions.save")}
            </Button>
          ) : (
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
              {t("organisation.oidc.actions.done")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
