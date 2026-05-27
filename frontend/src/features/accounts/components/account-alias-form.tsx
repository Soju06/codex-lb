import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { AccountSummary } from "@/features/accounts/schemas";

export type AccountAliasFormProps = {
  account: AccountSummary;
  busy: boolean;
  onSetAlias: (accountId: string, alias: string | null) => Promise<unknown>;
};

export function AccountAliasForm({ account, busy, onSetAlias }: AccountAliasFormProps) {
  const [alias, setAlias] = useState(account.alias ?? "");

  const normalized = alias.trim();
  const storedAlias = account.alias ?? "";
  const dirty = normalized !== storedAlias;
  const canClear = storedAlias.length > 0;

  return (
    <form
      className="rounded-lg border bg-muted/20 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        void onSetAlias(account.accountId, normalized === "" ? null : normalized);
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor="account-alias">Account alias</Label>
        <p className="text-xs text-muted-foreground">
          Use a local label to distinguish accounts that share the same email.
        </p>
      </div>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row">
        <Input
          id="account-alias"
          maxLength={255}
          placeholder="Personal Plus"
          value={alias}
          onChange={(event) => setAlias(event.target.value)}
          disabled={busy}
        />
        <Button type="submit" size="sm" className="h-9 shrink-0" disabled={busy || !dirty}>
          Save alias
        </Button>
        {canClear ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-9 shrink-0"
            disabled={busy}
            onClick={() => {
              setAlias("");
              void onSetAlias(account.accountId, null);
            }}
          >
            Clear alias
          </Button>
        ) : null}
      </div>
    </form>
  );
}
