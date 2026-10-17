import { ArrowDown, ArrowUp, GripVertical, ListOrdered } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { AuthProvider, RoleMapping } from "@/features/organisation/api";
import {
  organisationErrorMessage,
  useOrganisationMutations,
  useRefusedSignIns,
} from "@/features/organisation/hooks";
import {
  CLAIM_EMAIL_DOMAIN,
  CLAIM_GROUPS,
  REFUSED_WINDOW_DAYS,
  moveInOrder,
  normalizeEmailDomain,
  suggestedEmailDomain,
  type PickerRole,
} from "@/features/organisation/rules";
import { RolePicker, RoleSelectItems } from "@/features/settings/components/access/role-picker";
import { RefusedSignInsSheet } from "@/features/settings/components/organisation/refused-sign-ins-sheet";

const CLAIMS = [CLAIM_GROUPS, CLAIM_EMAIL_DOMAIN] as const;

function defaultRoleId(roles: readonly PickerRole[]): string {
  return (roles.find((role) => role.slug === "viewer") ?? roles[0])?.id ?? "";
}

export type GroupRulesCardProps = {
  provider: AuthProvider;
  roles: readonly PickerRole[];
  rules: readonly RoleMapping[];
  mutations: ReturnType<typeof useOrganisationMutations>;
  /** `audit:read`; without it the refused-sign-ins line and its view are not drawn. */
  canReadAudit: boolean;
  /** The `#organisation-refused` deep link lands with the list already open. */
  refusedOpen?: boolean;
  disabled?: boolean;
};

/**
 * The rules that turn what the proxy says about someone into what they can do
 * here. The list is ordered: the first rule that matches wins, so the order is
 * the whole meaning of the table and can be changed by dragging a row or with
 * the move buttons next to it (the keyboard path is not optional).
 */
export function GroupRulesCard({
  provider,
  roles,
  rules,
  mutations,
  canReadAudit,
  refusedOpen = false,
  disabled = false,
}: GroupRulesCardProps) {
  const { t } = useTranslation();
  const refusedQuery = useRefusedSignIns(canReadAudit);
  const [refusedListOpen, setRefusedListOpen] = useState(refusedOpen);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [claimName, setClaimName] = useState<string>(rules.length === 0 ? CLAIM_EMAIL_DOMAIN : CLAIM_GROUPS);
  const [claimValue, setClaimValue] = useState("");
  const [newRoleId, setNewRoleId] = useState("");

  const busy = disabled || mutations.busy;
  const error =
    mutations.createMapping.error ||
    mutations.updateMapping.error ||
    mutations.removeMapping.error ||
    mutations.reorderMappings.error;
  const errorMessage = error ? organisationErrorMessage(error, t) : null;
  const refusedEntries = refusedQuery.data ?? [];
  const refusedCount = refusedEntries.length;
  const suggestion = suggestedEmailDomain(refusedEntries);
  const addRoleId = newRoleId || defaultRoleId(roles);
  const typedValue = claimValue || (claimName === CLAIM_EMAIL_DOMAIN ? suggestion : "");
  const canAdd = typedValue.trim().length > 0 && addRoleId !== "" && !busy;

  const add = () => {
    if (!canAdd) {
      return;
    }
    const value = claimName === CLAIM_EMAIL_DOMAIN ? normalizeEmailDomain(typedValue) : typedValue.trim();
    void mutations.createMapping
      .mutateAsync({
        provider: provider.kind,
        providerKey: provider.providerKey,
        claimName,
        claimValue: value,
        roleId: addRoleId,
      })
      .then(() => {
        setClaimValue("");
        setNewRoleId("");
      })
      .catch(() => undefined);
  };

  const applyOrder = (ordered: readonly RoleMapping[]) => {
    void mutations.reorderMappings
      .mutateAsync({
        provider: provider.kind,
        providerKey: provider.providerKey,
        ids: ordered.map((rule) => rule.id),
      })
      .catch(() => undefined);
  };

  const move = (from: number, to: number) => {
    if (to < 0 || to >= rules.length) {
      return;
    }
    applyOrder(moveInOrder(rules, from, to));
  };

  const addForm = (
    <div className="flex flex-col gap-2 sm:flex-row" data-testid="organisation-rule-add">
      <Select value={claimName} onValueChange={setClaimName} disabled={busy}>
        <SelectTrigger className="sm:w-44" aria-label={t("organisation.rules.add.claimLabel")}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {CLAIMS.map((claim) => (
            <SelectItem key={claim} value={claim}>
              {t(`organisation.rules.claims.${claim}`)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Input
        value={typedValue}
        onChange={(event) => setClaimValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            add();
          }
        }}
        aria-label={t("organisation.rules.add.valueLabel")}
        placeholder={
          claimName === CLAIM_EMAIL_DOMAIN
            ? t("organisation.rules.add.domainPlaceholder")
            : t("organisation.rules.add.groupPlaceholder")
        }
        className="h-9 text-xs"
        disabled={busy}
      />
      <RolePicker
        value={addRoleId}
        onValueChange={setNewRoleId}
        roles={roles}
        disabled={busy}
        ariaLabel={t("organisation.rules.add.roleLabel")}
        className="sm:w-44"
      />
      <Button type="button" size="sm" className="h-9 text-xs" onClick={add} disabled={!canAdd}>
        {t("organisation.rules.add.submit")}
      </Button>
    </div>
  );

  return (
    <section id="organisation-rules" className="scroll-mt-16 space-y-3 rounded-xl border bg-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <ListOrdered className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">{t("organisation.rules.title")}</h3>
            <p className="text-xs text-muted-foreground">{t("organisation.rules.description")}</p>
          </div>
        </div>
        {canReadAudit && refusedQuery.isSuccess && refusedCount > 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="organisation-refused-line">
            {t("organisation.rules.refused", { count: refusedCount, days: REFUSED_WINDOW_DAYS })}{" "}
            <Button
              type="button"
              variant="link"
              size="sm"
              className="h-auto p-0 text-xs"
              onClick={() => setRefusedListOpen(true)}
            >
              {t("organisation.rules.refusedView")}
            </Button>
          </p>
        ) : null}
      </div>

      {errorMessage ? <AlertMessage variant="error">{errorMessage}</AlertMessage> : null}

      {rules.length === 0 ? (
        <div
          className="space-y-3 rounded-xl border border-dashed border-border/60 p-5"
          data-testid="organisation-rules-empty"
        >
          <p className="text-sm font-medium text-muted-foreground">{t("organisation.rules.empty.title")}</p>
          <p className="text-xs text-muted-foreground/80">
            {provider.unknownIdentityRoleId === null
              ? t("organisation.rules.empty.refuses")
              : t("organisation.rules.empty.admits", {
                  role: roles.find((role) => role.id === provider.unknownIdentityRoleId)?.name ?? "",
                })}
          </p>
          <p className="text-xs text-muted-foreground/80">{t("organisation.rules.empty.reevaluation")}</p>
          <p className="text-xs text-muted-foreground/80">{t("organisation.rules.empty.quickAdd")}</p>
          {addForm}
        </div>
      ) : (
        <>
          {addForm}
          <div className="overflow-x-auto rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[120px]">{t("organisation.rules.table.order")}</TableHead>
                  <TableHead>{t("organisation.rules.table.matches")}</TableHead>
                  <TableHead className="w-[200px]">{t("organisation.rules.table.role")}</TableHead>
                  <TableHead className="w-[96px] text-right">{t("apiKeys.table.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rules.map((rule, index) => (
                  <TableRow
                    key={rule.id}
                    data-testid={`organisation-rule-${rule.claimValue}`}
                    draggable={!busy}
                    onDragStart={() => setDragIndex(index)}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={() => {
                      if (dragIndex !== null && dragIndex !== index) {
                        applyOrder(moveInOrder(rules, dragIndex, index));
                      }
                      setDragIndex(null);
                    }}
                    onDragEnd={() => setDragIndex(null)}
                  >
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <GripVertical className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                        <span className="text-xs tabular-nums text-muted-foreground">{index + 1}</span>
                        <Button
                          type="button"
                          size="icon-sm"
                          variant="ghost"
                          aria-label={t("organisation.rules.actions.moveUp", { value: rule.claimValue })}
                          disabled={busy || index === 0}
                          onClick={() => move(index, index - 1)}
                        >
                          <ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
                        </Button>
                        <Button
                          type="button"
                          size="icon-sm"
                          variant="ghost"
                          aria-label={t("organisation.rules.actions.moveDown", { value: rule.claimValue })}
                          disabled={busy || index === rules.length - 1}
                          onClick={() => move(index, index + 1)}
                        >
                          <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
                        </Button>
                      </div>
                    </TableCell>
                    <TableCell className="text-xs">
                      <span className="text-muted-foreground">{t(`organisation.rules.claims.${rule.claimName}`)}</span>{" "}
                      <span className="font-mono font-medium">{rule.claimValue}</span>
                    </TableCell>
                    <TableCell>
                      <Select
                        value={rule.roleId}
                        disabled={busy || roles.length === 0}
                        onValueChange={(roleId) =>
                          void mutations.updateMapping
                            .mutateAsync({ mappingId: rule.id, payload: { roleId } })
                            .catch(() => undefined)
                        }
                      >
                        <SelectTrigger
                          className="h-8"
                          aria-label={t("organisation.rules.actions.role", { value: rule.claimValue })}
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <RoleSelectItems roles={roles} />
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="text-destructive hover:text-destructive"
                        disabled={busy}
                        onClick={() => void mutations.removeMapping.mutateAsync(rule.id).catch(() => undefined)}
                      >
                        {t("common.actions.remove")}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      )}

      <RefusedSignInsSheet
        open={refusedListOpen}
        onOpenChange={setRefusedListOpen}
        entries={refusedEntries}
        loading={refusedQuery.isLoading}
      />
    </section>
  );
}
