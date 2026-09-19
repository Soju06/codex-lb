import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { ModelMultiSelect } from "@/features/api-keys/components/model-multi-select";
import type {
  TeamMember,
  TeamMemberCreateRequest,
  TeamMemberUpdateRequest,
} from "@/features/team/schemas";

type CapField = {
  key: "costCapDayUsd" | "costCapWeekUsd" | "costCapMonthUsd" | "tokenCapDay" | "tokenCapWeek" | "tokenCapMonth";
  label: string;
};

const COST_CAPS: CapField[] = [
  { key: "costCapDayUsd", label: "Cost cap / day ($)" },
  { key: "costCapWeekUsd", label: "Cost cap / week ($)" },
  { key: "costCapMonthUsd", label: "Cost cap / month ($)" },
];

const TOKEN_CAPS: CapField[] = [
  { key: "tokenCapDay", label: "Token cap / day" },
  { key: "tokenCapWeek", label: "Token cap / week" },
  { key: "tokenCapMonth", label: "Token cap / month" },
];

type FormState = {
  name: string;
  email: string;
  notes: string;
  active: boolean;
  allowedModels: string[];
  caps: Record<CapField["key"], string>;
};

const EMPTY_CAPS: Record<CapField["key"], string> = {
  costCapDayUsd: "",
  costCapWeekUsd: "",
  costCapMonthUsd: "",
  tokenCapDay: "",
  tokenCapWeek: "",
  tokenCapMonth: "",
};

function toFormState(member: TeamMember | null): FormState {
  if (member === null) {
    return { name: "", email: "", notes: "", active: true, allowedModels: [], caps: { ...EMPTY_CAPS } };
  }
  return {
    name: member.name,
    email: member.email ?? "",
    notes: member.notes ?? "",
    active: member.status === "active",
    allowedModels: member.allowedModels ?? [],
    caps: {
      costCapDayUsd: member.costCapDayUsd === null ? "" : String(member.costCapDayUsd),
      costCapWeekUsd: member.costCapWeekUsd === null ? "" : String(member.costCapWeekUsd),
      costCapMonthUsd: member.costCapMonthUsd === null ? "" : String(member.costCapMonthUsd),
      tokenCapDay: member.tokenCapDay === null ? "" : String(member.tokenCapDay),
      tokenCapWeek: member.tokenCapWeek === null ? "" : String(member.tokenCapWeek),
      tokenCapMonth: member.tokenCapMonth === null ? "" : String(member.tokenCapMonth),
    },
  };
}

function parseCap(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return null;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export type TeamMemberDrawerProps = {
  open: boolean;
  busy: boolean;
  member: TeamMember | null;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: TeamMemberCreateRequest | TeamMemberUpdateRequest) => Promise<void>;
};

export function TeamMemberDrawer({
  open,
  busy,
  member,
  onOpenChange,
  onSubmit,
}: TeamMemberDrawerProps) {
  const [form, setForm] = useState<FormState>(() => toFormState(member));
  const [loadedFor, setLoadedFor] = useState<string | null>(null);

  // Reset the form whenever the drawer opens, for a new member or the same one again.
  const openFor = open ? (member?.id ?? "new") : null;
  if (openFor !== loadedFor) {
    setLoadedFor(openFor);
    if (openFor !== null) {
      setForm(toFormState(member));
    }
  }

  const editing = member !== null;
  const canSubmit = form.name.trim().length > 0 && !busy;

  const submit = async () => {
    if (!canSubmit) {
      return;
    }
    const payload: TeamMemberCreateRequest & TeamMemberUpdateRequest = {
      name: form.name.trim(),
      email: form.email.trim() === "" ? null : form.email.trim(),
      notes: form.notes.trim() === "" ? null : form.notes.trim(),
      status: form.active ? "active" : "suspended",
      allowedModels: form.allowedModels.length === 0 ? null : form.allowedModels,
      costCapDayUsd: parseCap(form.caps.costCapDayUsd),
      costCapWeekUsd: parseCap(form.caps.costCapWeekUsd),
      costCapMonthUsd: parseCap(form.caps.costCapMonthUsd),
      tokenCapDay: parseCap(form.caps.tokenCapDay),
      tokenCapWeek: parseCap(form.caps.tokenCapWeek),
      tokenCapMonth: parseCap(form.caps.tokenCapMonth),
    };
    await onSubmit(payload);
  };

  const setCap = (key: CapField["key"], value: string) => {
    setForm((current) => ({ ...current, caps: { ...current.caps, [key]: value } }));
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{editing ? "Edit team member" : "Add team member"}</SheetTitle>
          <SheetDescription>
            Caps apply to every key attached to this member. Leave a cap blank for no limit.
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-4 px-4">
          <div className="space-y-1.5">
            <Label htmlFor="team-member-name">Name</Label>
            <Input
              id="team-member-name"
              value={form.name}
              disabled={busy}
              onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="team-member-email">Email</Label>
            <Input
              id="team-member-email"
              type="email"
              value={form.email}
              disabled={busy}
              onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            {COST_CAPS.map((cap) => (
              <div key={cap.key} className="space-y-1.5">
                <Label htmlFor={`team-${cap.key}`} className="text-xs">
                  {cap.label}
                </Label>
                <Input
                  id={`team-${cap.key}`}
                  type="number"
                  min={0}
                  step="0.01"
                  inputMode="decimal"
                  value={form.caps[cap.key]}
                  disabled={busy}
                  onChange={(event) => setCap(cap.key, event.target.value)}
                />
              </div>
            ))}
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            {TOKEN_CAPS.map((cap) => (
              <div key={cap.key} className="space-y-1.5">
                <Label htmlFor={`team-${cap.key}`} className="text-xs">
                  {cap.label}
                </Label>
                <Input
                  id={`team-${cap.key}`}
                  type="number"
                  min={0}
                  step="1"
                  inputMode="numeric"
                  value={form.caps[cap.key]}
                  disabled={busy}
                  onChange={(event) => setCap(cap.key, event.target.value)}
                />
              </div>
            ))}
          </div>

          <div className="space-y-1.5">
            <Label>Allowed models</Label>
            <ModelMultiSelect
              value={form.allowedModels}
              onChange={(allowedModels) => setForm((current) => ({ ...current, allowedModels }))}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="team-member-notes">Notes</Label>
            <Input
              id="team-member-notes"
              value={form.notes}
              disabled={busy}
              onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))}
            />
          </div>

          <div className="flex items-center justify-between rounded-lg border p-3">
            <div className="space-y-1">
              <p className="text-sm font-medium">Active</p>
              <p className="text-xs text-muted-foreground">
                Suspended members are blocked at the proxy until reactivated.
              </p>
            </div>
            <Switch
              checked={form.active}
              disabled={busy}
              aria-label="Active"
              onCheckedChange={(active) => setForm((current) => ({ ...current, active }))}
            />
          </div>
        </div>

        <SheetFooter>
          <Button type="button" disabled={!canSubmit} onClick={() => void submit()}>
            {editing ? "Save" : "Add member"}
          </Button>
          <Button type="button" variant="outline" disabled={busy} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
