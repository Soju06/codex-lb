import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useState, type ComponentProps } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Spinner, SpinnerBlock } from "@/components/ui/spinner";
import { describeInvite } from "@/features/auth/api";
import { AuthScreenFrame } from "@/features/auth/components/auth-screen-frame";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { InviteAcceptRequestSchema, type InviteDescription } from "@/features/auth/schemas";
import { ApiError } from "@/lib/api-client";
import { getErrorMessage } from "@/utils/errors";

const InviteFormSchema = InviteAcceptRequestSchema.omit({ token: true })
  .extend({ confirmPassword: z.string() })
  .refine((values) => values.password === values.confirmPassword, {
    message: "auth.invite.validation.passwordMismatch",
    path: ["confirmPassword"],
  });

type InviteFormValues = z.infer<typeof InviteFormSchema>;

type Phase =
  | { kind: "checking" }
  | { kind: "expired" }
  | { kind: "error"; message: string }
  | { kind: "form"; invite: InviteDescription };

export type InviteAcceptScreenProps = {
  token: string;
};

/**
 * Public `/invite/:token` screen. Only a 404 means the link is dead (every
 * invalid token shares one message); other lookup failures can be retried.
 * Any resolvable account session (even one still pending TOTP) is told to log
 * out first, and the server refuses the accept with 409 for it as well. After
 * a successful accept the returned session is applied and the gate takes over
 * (including the authenticator enrollment step when the new session needs it).
 */
export function InviteAcceptScreen({ token }: InviteAcceptScreenProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const refreshSession = useAuthStore((state) => state.refreshSession);
  const logout = useAuthStore((state) => state.logout);
  const loading = useAuthStore((state) => state.loading);
  const signedInUser = useAuthStore((state) => state.user);
  const [phase, setPhase] = useState<Phase>({ kind: "checking" });
  const [attempt, setAttempt] = useState(0);
  const [serverSaysSignedIn, setServerSaysSignedIn] = useState(false);

  // The gate keys this screen by token, so a new token mounts a fresh "checking" state.
  useEffect(() => {
    let cancelled = false;
    describeInvite(token)
      .then((invite) => {
        if (!cancelled) setPhase({ kind: "form", invite });
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        if (caught instanceof ApiError && caught.status === 404) {
          setPhase({ kind: "expired" });
        } else {
          setPhase({ kind: "error", message: getErrorMessage(caught) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token, attempt]);

  const handleAlreadySignedIn = async () => {
    if (!useAuthStore.getState().user) {
      await refreshSession().catch(() => undefined);
    }
    setServerSaysSignedIn(true);
  };

  const showSignedIn = phase.kind === "form" && (signedInUser !== null || serverSaysSignedIn);
  const subtitle =
    phase.kind === "form"
      ? phase.invite.inviterDisplayName
        ? t("auth.invite.invitedBy", { inviter: phase.invite.inviterDisplayName, role: phase.invite.roleName })
        : t("auth.invite.invitedAs", { role: phase.invite.roleName })
      : t("auth.invite.subtitle");

  return (
    <AuthScreenFrame title={t("auth.invite.title")} subtitle={subtitle}>
      <div className="rounded-2xl border bg-card p-6 shadow-[var(--shadow-md)]">
        {phase.kind === "checking" ? (
          <div className="flex flex-col items-center gap-3 py-4 text-sm text-muted-foreground">
            <SpinnerBlock />
            <p>{t("auth.invite.checking")}</p>
          </div>
        ) : null}

        {phase.kind === "expired" ? <p className="text-sm text-muted-foreground">{t("auth.invite.expired")}</p> : null}

        {phase.kind === "error" ? (
          <div className="space-y-4">
            <AlertMessage variant="error">{phase.message}</AlertMessage>
            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={() => {
                setPhase({ kind: "checking" });
                setAttempt((count) => count + 1);
              }}
            >
              {t("auth.invite.retry")}
            </Button>
          </div>
        ) : null}

        {showSignedIn ? (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              {t("auth.invite.signedIn", { username: signedInUser?.username ?? "" })}
            </p>
            <Button
              type="button"
              variant="outline"
              className="w-full"
              disabled={loading}
              onClick={() => {
                setServerSaysSignedIn(false);
                void logout();
              }}
            >
              {t("common.logout")}
            </Button>
          </div>
        ) : null}

        {phase.kind === "form" && !showSignedIn ? (
          <InviteForm
            token={token}
            invite={phase.invite}
            onAccepted={() => navigate("/", { replace: true })}
            onAlreadySignedIn={handleAlreadySignedIn}
          />
        ) : null}
      </div>
    </AuthScreenFrame>
  );
}

type InviteFormProps = {
  token: string;
  invite: InviteDescription;
  onAccepted: () => void;
  onAlreadySignedIn: () => Promise<void>;
};

type InviteFieldName = "username" | "displayName" | "password" | "confirmPassword";

function InviteForm({ token, invite, onAccepted, onAlreadySignedIn }: InviteFormProps) {
  const { t } = useTranslation();
  const acceptInvite = useAuthStore((state) => state.acceptInvite);
  const form = useForm<InviteFormValues>({
    resolver: zodResolver(InviteFormSchema),
    defaultValues: { username: invite.suggestedUsername, displayName: "", password: "", confirmPassword: "" },
  });
  const busy = form.formState.isSubmitting;
  const rootError = form.formState.errors.root?.message;

  const handleSubmit = async (values: InviteFormValues) => {
    form.clearErrors("root");
    try {
      await acceptInvite({
        token,
        username: invite.usernameLocked ? invite.suggestedUsername : values.username,
        password: values.password,
        displayName: values.displayName?.trim() ? values.displayName.trim() : undefined,
      });
      onAccepted();
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "already_signed_in") {
        await onAlreadySignedIn();
      } else if (caught instanceof ApiError && caught.code === "username_taken") {
        form.setError("username", { message: t("auth.invite.usernameTaken") });
      } else if (caught instanceof ApiError && caught.code === "username_locked") {
        form.setError("username", { message: getErrorMessage(caught) });
      } else if (caught instanceof ApiError && caught.status === 404) {
        form.setError("root", { message: t("auth.invite.expired") });
      } else {
        form.setError("root", { message: getErrorMessage(caught) });
      }
    }
  };

  const field = (name: InviteFieldName, label: string, input: Omit<ComponentProps<typeof Input>, "name">, hint?: string) => (
    <FormField
      control={form.control}
      name={name}
      render={({ field: controller }) => (
        <FormItem>
          <FormLabel className="text-xs font-medium">{label}</FormLabel>
          <FormControl>
            <Input {...controller} {...input} disabled={busy} />
          </FormControl>
          {hint ? <FormDescription>{hint}</FormDescription> : null}
          <FormMessage />
        </FormItem>
      )}
    />
  );

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-4">
        {rootError ? <AlertMessage variant="error">{rootError}</AlertMessage> : null}
        {field(
          "username",
          t("auth.invite.usernameLabel"),
          { type: "text", autoComplete: "username", autoCapitalize: "none", spellCheck: false, readOnly: invite.usernameLocked },
          invite.usernameLocked ? t("auth.invite.usernameLockedHint") : undefined,
        )}
        {field("displayName", t("auth.invite.displayNameLabel"), { type: "text", autoComplete: "name" })}
        {field("password", t("auth.invite.passwordLabel"), {
          type: "password",
          autoComplete: "new-password",
          placeholder: t("auth.invite.passwordPlaceholder"),
        })}
        {field("confirmPassword", t("auth.invite.confirmPasswordLabel"), { type: "password", autoComplete: "new-password" })}
        <Button type="submit" className="press-scale mt-1 w-full" disabled={busy}>
          {busy ? <Spinner size="sm" className="mr-2" /> : null}
          {t("auth.invite.submit")}
        </Button>
      </form>
    </Form>
  );
}
