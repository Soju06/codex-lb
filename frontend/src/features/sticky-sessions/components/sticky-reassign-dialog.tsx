import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { listAccounts } from "@/features/accounts/api";
import { reassignStickySession } from "@/features/sticky-sessions/api";
import type { StickySessionEntry } from "@/features/sticky-sessions/schemas";
import { usePrivacyStore } from "@/hooks/use-privacy";

export function StickyReassignDialog({ entry, onClose }: { entry: StickySessionEntry; onClose: () => void }) {
  const { t } = useTranslation();
  const blurred = usePrivacyStore((state) => state.blurred);
  const queryClient = useQueryClient();
  const [accountId, setAccountId] = useState("");
  const accounts = useQuery({ queryKey: ["accounts", "list"], queryFn: listAccounts });
  const mutation = useMutation({
    mutationFn: () => reassignStickySession({
      key: entry.key, kind: "prompt_cache", expectedAccountId: entry.accountId!, accountId,
    }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["sticky-sessions", "list"] });
      onClose();
    },
    onError: () => { void queryClient.invalidateQueries({ queryKey: ["sticky-sessions", "list"] }); },
  });
  const targets = (accounts.data?.accounts ?? []).filter((account) =>
    account.accountId !== entry.accountId && account.routingPolicy !== "preserve" &&
    (account.status === "active" || account.status === "reauth_required"),
  );
  return (
    <Dialog open onOpenChange={(open) => { if (!open && !mutation.isPending) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("stickySessions.switch.title", { defaultValue: "Switch account" })}</DialogTitle>
          <DialogDescription>{t("stickySessions.switch.description", {
            defaultValue: "Change this soft cache preference for future requests. Running requests and hard continuations (files, previous responses, turn state) keep their owner. Normal model, quota and API-key restrictions still apply.",
          })}</DialogDescription>
        </DialogHeader>
        <p className="break-all text-xs font-mono">{entry.key}</p>
        <p className="text-sm">{t("stickySessions.switch.current", { defaultValue: "Current account" })}: <span className={blurred ? "privacy-blur" : undefined}>{entry.displayName}</span></p>
        <Select value={accountId} onValueChange={setAccountId} disabled={mutation.isPending || accounts.isFetching}>
          <SelectTrigger className="w-full" aria-label={t("stickySessions.switch.target", { defaultValue: "Target account" })}>
            <SelectValue placeholder={t("stickySessions.switch.choose", { defaultValue: "Choose another account" })} />
          </SelectTrigger>
          <SelectContent>{targets.map((account) => (
            <SelectItem key={account.accountId} value={account.accountId}><span className={blurred ? "privacy-blur" : undefined}>{account.displayName}</span> · {account.accountId.slice(-8)}</SelectItem>
          ))}</SelectContent>
        </Select>
        {accounts.error || mutation.error ? <AlertMessage variant="error">{(accounts.error || mutation.error)?.message}</AlertMessage> : null}
        {!accounts.isFetching && !accounts.error && targets.length === 0 ? <p className="text-sm text-muted-foreground">
          {t("stickySessions.switch.empty", { defaultValue: "No other available account. Resume or repair an account first." })}
        </p> : null}
        <Button disabled={!accountId || mutation.isPending || !entry.accountId} onClick={() => mutation.mutate()}>
          {t("stickySessions.switch.title", { defaultValue: "Switch account" })}
        </Button>
      </DialogContent>
    </Dialog>
  );
}
