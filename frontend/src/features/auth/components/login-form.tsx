import { zodResolver } from "@hookform/resolvers/zod";
import { Eye, Lock } from "lucide-react";
import { useForm } from "react-hook-form";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { LoginRequestSchema } from "@/features/auth/schemas";
import { useAuthStore } from "@/features/auth/hooks/use-auth";

export function LoginForm() {
  const login = useAuthStore((state) => state.login);
  const loginGuest = useAuthStore((state) => state.loginGuest);
  const loading = useAuthStore((state) => state.loading);
  const error = useAuthStore((state) => state.error);
  const clearError = useAuthStore((state) => state.clearError);
  const passwordRequired = useAuthStore((state) => state.passwordRequired);
  const guestAccessEnabled = useAuthStore((state) => state.guestAccessEnabled);
  const guestPasswordRequired = useAuthStore((state) => state.guestPasswordRequired);

  const form = useForm({
    resolver: zodResolver(LoginRequestSchema),
    defaultValues: { password: "" },
  });
  const guestForm = useForm({
    defaultValues: { password: "" },
  });

  const handleSubmit = async (values: { password: string }) => {
    clearError();
    await login(values.password);
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
              <h2 className="text-base font-semibold tracking-tight">Sign in</h2>
              <p className="text-sm text-muted-foreground">Enter your admin password to continue.</p>
            </div>

            <div className="mt-5">
              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs font-medium">Password</FormLabel>
                    <div className="relative">
                      <Lock
                        className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground/60"
                        aria-hidden="true"
                      />
                      <FormControl>
                        <Input
                          {...field}
                          type="password"
                          autoComplete="current-password"
                          placeholder="Enter password"
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
              Sign In
            </Button>
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
              <h3 className="text-sm font-semibold tracking-tight">Guest access</h3>
              <p className="text-xs text-muted-foreground">View the dashboard without admin controls.</p>
            </div>
            {guestPasswordRequired ? (
              <div className="mt-4">
                <FormField
                  control={guestForm.control}
                  name="password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs font-medium">Guest password</FormLabel>
                      <div className="relative">
                        <Eye className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground/60" aria-hidden="true" />
                        <FormControl>
                          <Input
                            {...field}
                            type="password"
                            autoComplete="current-password"
                            placeholder="Enter guest password"
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
              View as Guest
            </Button>
          </form>
        </Form>
      ) : null}
    </div>
  );
}
