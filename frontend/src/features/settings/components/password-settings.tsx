import { useState } from "react";
import type { FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { changePassword, removePassword, setupPassword } from "@/features/auth/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Password request failed";
}

export type PasswordSettingsProps = {
  disabled?: boolean;
};

export function PasswordSettings({ disabled = false }: PasswordSettingsProps) {
  const refreshSession = useAuthStore((state) => state.refreshSession);
  const [setupPasswordValue, setSetupPasswordValue] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [removePasswordValue, setRemovePasswordValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const lock = busy || disabled;

  const handleSetup = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      await setupPassword({ password: setupPasswordValue });
      await refreshSession();
      setMessage("Password configured.");
      setSetupPasswordValue("");
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleChange = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      await changePassword({ currentPassword, newPassword });
      setMessage("Password changed.");
      setCurrentPassword("");
      setNewPassword("");
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      await removePassword({ password: removePasswordValue });
      await refreshSession();
      setMessage("Password removed.");
      setRemovePasswordValue("");
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-3 rounded-xl border p-4">
      <div>
        <h3 className="text-sm font-semibold">Password</h3>
        <p className="text-xs text-muted-foreground">Setup, rotate, or remove dashboard password.</p>
      </div>

      {message ? <p className="rounded-md bg-emerald-500/10 px-2 py-1 text-xs text-emerald-700">{message}</p> : null}
      {error ? <p className="rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p> : null}

      <form className="space-y-2 rounded-md border p-3" onSubmit={handleSetup}>
        <Label htmlFor="setup-password">Setup password</Label>
        <Input
          id="setup-password"
          type="password"
          minLength={8}
          value={setupPasswordValue}
          onChange={(event) => setSetupPasswordValue(event.target.value)}
        />
        <Button type="submit" size="sm" disabled={lock || setupPasswordValue.length < 8}>
          Setup
        </Button>
      </form>

      <form className="space-y-2 rounded-md border p-3" onSubmit={handleChange}>
        <Label htmlFor="current-password">Current password</Label>
        <Input
          id="current-password"
          type="password"
          value={currentPassword}
          onChange={(event) => setCurrentPassword(event.target.value)}
        />
        <Label htmlFor="new-password">New password</Label>
        <Input
          id="new-password"
          type="password"
          minLength={8}
          value={newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
        />
        <Button
          type="submit"
          size="sm"
          variant="outline"
          disabled={lock || !currentPassword || newPassword.length < 8}
        >
          Change
        </Button>
      </form>

      <form className="space-y-2 rounded-md border p-3" onSubmit={handleRemove}>
        <Label htmlFor="remove-password">Confirm password to remove</Label>
        <Input
          id="remove-password"
          type="password"
          value={removePasswordValue}
          onChange={(event) => setRemovePasswordValue(event.target.value)}
        />
        <Button type="submit" size="sm" variant="destructive" disabled={lock || !removePasswordValue}>
          Remove password
        </Button>
      </form>
    </section>
  );
}
