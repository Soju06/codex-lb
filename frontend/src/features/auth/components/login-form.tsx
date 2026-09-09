import { zodResolver } from "@hookform/resolvers/zod";
import { Eye, Lock, User } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { readLastUsername } from "@/features/auth/last-username";
import { LoginRequestSchema } from "@/features/auth/schemas";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { ApiError } from "@/lib/api-client";

export function LoginForm() {
  const { t } = useTranslation();
  const login = useAuthStore((state) => state.login);
  const loginGuest = useAuthStore((state) => state.loginGuest);
  const loading = useAuthStore((state) => state.loading);
  const error = useAuthStore((state) => state.error);
  const clearError = useAuthStore((state) => state.clearError);
  const passwordRequired = useAuthStore((state) => state.passwordRequired);
  const usernameField = useAuthStore((state) => state.loginHint.usernameField);
  const guestAccessEnabled = useAuthStore((state) => state.guestAccessEnabled);
  const guestPasswordRequired = useAuthStore((state) => state.guestPasswordRequired);

  // A remembered non-default username keeps the field visible even when the
  // server says `hidden` (anti-flapping after the second account is removed
  // again, PLAN §4.4); the "different account" link and a `username_required`
  // answer reveal it too.
  const [lastUsername] = useState(readLastUsername);
  const [usernameRevealed, setUsernameRevealed] = useState(false);
  const showUsername =
    usernameField === "shown" || (lastUsername !== "" && lastUsername !== "admin") || usernameRevealed;

  const form = useForm({
    resolver: zodResolver(LoginRequestSchema),
    defaultValues: { username: lastUsername, password: "" },
  });
  const guestForm = useForm({
    defaultValues: { password: "" },
  });

  // Move focus into the field the moment it is revealed (link or server answer).
  useEffect(() => {
    if (usernameRevealed) {
      form.setFocus("username");
    }
  }, [usernameRevealed, form]);

  const handleSubmit = async (values: { username?: string; password: string }) => {
    clearError();
    const username = showUsername ? values.username?.trim() : undefined;
    try {
      await (username ? login(values.password, username) : login(values.password));
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "username_required") {
        clearError();
        setUsernameRevealed(true);
        form.setError("username", { message: t("auth.login.usernameRequired") });
      }
    }
  };

  const handleGuestSubmit = async (values: { password: string }) => {
    clearError();
    await loginGuest(values.password.trim() || undefined);
  };

  return (
    <div className="rounded-2xl border bg-card p-6 shadow-[var(--shadow-md)]">
      {passwordRequired ? (
        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)}>
            <div className="space-y-1.5">
              <h2 className="text-base font-semibold tracking-tight">{t("auth.login.heading")}</h2>
              <p className="text-sm text-muted-foreground">
                {showUsername ? t("auth.login.subheadingWithUsername") : t("auth.login.subheading")}
              </p>
            </div>

            {showUsername ? (
              <div className="mt-5">
                <FormField
                  control={form.control}
                  name="username"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs font-medium">{t("auth.login.usernameLabel")}</FormLabel>
                      <div className="relative">
                        <User className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground/60" aria-hidden="true" />
                        <FormControl>
                          <Input
                            {...field}
                            type="text"
                            autoComplete="username"
                            autoCapitalize="none"
                            spellCheck={false}
                            placeholder={t("auth.login.usernamePlaceholder")}
                            disabled={loading}
                            className="pl-9"
                          />
                        </FormControl>
                      </div>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            ) : null}

            <div className={showUsername ? "mt-4" : "mt-5"}>
              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs font-medium">{t("auth.login.passwordLabel")}</FormLabel>
                    <div className="relative">
                      <Lock className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground/60" aria-hidden="true" />
                      <FormControl>
                        <Input
                          {...field}
                          type="password"
                          autoComplete="current-password"
                          placeholder={t("auth.login.passwordPlaceholder")}
                          disabled={loading}
                          className="pl-9"
                        />
                      </FormControl>
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <Button type="submit" className="press-scale mt-5 w-full" disabled={loading}>
              {loading ? <Spinner size="sm" className="mr-2" /> : null}
              {t("auth.login.submit")}
            </Button>

            {showUsername ? null : (
              <button
                type="button"
                className="mt-3 w-full text-center text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                onClick={() => setUsernameRevealed(true)}
                disabled={loading}
              >
                {t("auth.login.differentAccount")}
              </button>
            )}
          </form>
        </Form>
      ) : null}

      {error ? <AlertMessage variant="error" className="mt-4">{error}</AlertMessage> : null}

      {guestAccessEnabled ? (
        <Form {...guestForm}>
          <form
            onSubmit={guestForm.handleSubmit(handleGuestSubmit)}
            className={passwordRequired ? "mt-5 border-t pt-5" : ""}
          >
            <div className="space-y-1.5">
              <h3 className="text-sm font-semibold tracking-tight">{t("auth.guest.heading")}</h3>
              <p className="text-xs text-muted-foreground">{t("auth.guest.subheading")}</p>
            </div>

            {guestPasswordRequired ? (
              <div className="mt-4">
                <FormField
                  control={guestForm.control}
                  name="password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs font-medium">{t("auth.guest.passwordLabel")}</FormLabel>
                      <div className="relative">
                        <Eye className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground/60" aria-hidden="true" />
                        <FormControl>
                          <Input
                            {...field}
                            type="password"
                            autoComplete="current-password"
                            placeholder={t("auth.guest.passwordPlaceholder")}
                            disabled={loading}
                            className="pl-9"
                          />
                        </FormControl>
                      </div>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            ) : null}

            <Button type="submit" variant="outline" className="press-scale mt-4 w-full" disabled={loading}>
              {t("auth.guest.submit")}
            </Button>
          </form>
        </Form>
      ) : null}
    </div>
  );
}
