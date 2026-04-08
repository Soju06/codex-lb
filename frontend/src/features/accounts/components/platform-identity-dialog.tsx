import { useMemo, useState } from "react";
import type { FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import {
  PLATFORM_ROUTE_FAMILY_ORDER,
  PLATFORM_ROUTE_OPTIONS,
  shouldIncludeRouteFamily,
  type CheckedState,
} from "@/features/accounts/components/platform-identity-route-options";
import type {
  AccountSummary,
  PlatformIdentityCreateRequest,
  PlatformIdentityUpdateRequest,
  PlatformRouteFamily,
} from "@/features/accounts/schemas";

function normalizeOptionalText(value: string): string | null {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function normalizeEligibleRouteFamilies(value: PlatformRouteFamily[]): PlatformRouteFamily[] {
  const next = new Set(value);
  return PLATFORM_ROUTE_FAMILY_ORDER.filter((routeFamily) => next.has(routeFamily));
}

function areRouteFamiliesEqual(left: PlatformRouteFamily[], right: PlatformRouteFamily[]): boolean {
  if (left.length !== right.length) {
    return false;
  }
  return left.every((value, index) => value === right[index]);
}

type PlatformIdentityFormState = {
  label: string;
  apiKey: string;
  organization: string;
  project: string;
  eligibleRouteFamilies: PlatformRouteFamily[];
};

function createPlatformIdentityFormState({
  isEdit,
  label,
  organization,
  project,
  eligibleRouteFamilies,
}: {
  isEdit: boolean;
  label: string;
  organization: string | null;
  project: string | null;
  eligibleRouteFamilies: PlatformRouteFamily[];
}): PlatformIdentityFormState {
  if (!isEdit) {
    return {
      label: "",
      apiKey: "",
      organization: "",
      project: "",
      eligibleRouteFamilies: [],
    };
  }
  return {
    label,
    apiKey: "",
    organization: organization ?? "",
    project: project ?? "",
    eligibleRouteFamilies,
  };
}

export type PlatformIdentityDialogProps = {
  open: boolean;
  busy: boolean;
  error: string | null;
  mode?: "create" | "edit";
  account?: AccountSummary | null;
  prerequisiteSatisfied?: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (
    payload: PlatformIdentityCreateRequest | PlatformIdentityUpdateRequest,
  ) => Promise<void>;
};

export function PlatformIdentityDialog({
  open,
  busy,
  error,
  mode = "create",
  account = null,
  prerequisiteSatisfied = true,
  onOpenChange,
  onSubmit,
}: PlatformIdentityDialogProps) {
  const isEdit = mode === "edit";
  const initialLabel = account ? account.label ?? account.displayName ?? account.email : "";
  const initialOrganization = account?.organization ?? null;
  const initialProject = account?.project ?? null;
  const initialEligibleRouteFamilies = useMemo(
    () => normalizeEligibleRouteFamilies(account?.eligibleRouteFamilies ?? []),
    [account?.eligibleRouteFamilies],
  );

  const handleOpenChange = (nextOpen: boolean) => {
    onOpenChange(nextOpen);
  };

  const formKey = [
    mode,
    account?.accountId ?? "create",
    initialLabel,
    initialOrganization ?? "",
    initialProject ?? "",
    initialEligibleRouteFamilies.join(","),
  ].join(":");

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit OpenAI Platform API key" : "Add OpenAI Platform API key"}</DialogTitle>
          <DialogDescription>
            {isEdit ? (
              <>
                Update the fallback-only upstream identity for <code>/v1/models</code> and stateless HTTP{" "}
                <code>/v1/responses</code>. ChatGPT accounts stay primary, and this key is used only when the
                compatible ChatGPT pool is unhealthy under the primary or secondary drain thresholds.
              </>
            ) : (
              <>
                Register a fallback-only upstream identity for <code>/v1/models</code> and stateless HTTP{" "}
                <code>/v1/responses</code>. ChatGPT accounts stay primary, and this key is used only when the
                compatible ChatGPT pool is unhealthy under the primary or secondary drain thresholds.
              </>
            )}
          </DialogDescription>
        </DialogHeader>
        {open ? (
          <PlatformIdentityDialogForm
            key={formKey}
            busy={busy}
            error={error}
            mode={mode}
            account={account}
            prerequisiteSatisfied={prerequisiteSatisfied}
            initialLabel={initialLabel}
            initialOrganization={initialOrganization}
            initialProject={initialProject}
            initialEligibleRouteFamilies={initialEligibleRouteFamilies}
            onOpenChange={onOpenChange}
            onSubmit={onSubmit}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

type PlatformIdentityDialogFormProps = {
  busy: boolean;
  error: string | null;
  mode: "create" | "edit";
  account: AccountSummary | null;
  prerequisiteSatisfied: boolean;
  initialLabel: string;
  initialOrganization: string | null;
  initialProject: string | null;
  initialEligibleRouteFamilies: PlatformRouteFamily[];
  onOpenChange: (open: boolean) => void;
  onSubmit: (
    payload: PlatformIdentityCreateRequest | PlatformIdentityUpdateRequest,
  ) => Promise<void>;
};

function PlatformIdentityDialogForm({
  busy,
  error,
  mode,
  account,
  prerequisiteSatisfied,
  initialLabel,
  initialOrganization,
  initialProject,
  initialEligibleRouteFamilies,
  onOpenChange,
  onSubmit,
}: PlatformIdentityDialogFormProps) {
  const isEdit = mode === "edit";
  const [formState, setFormState] = useState(() =>
    createPlatformIdentityFormState({
      isEdit,
      label: initialLabel,
      organization: initialOrganization,
      project: initialProject,
      eligibleRouteFamilies: initialEligibleRouteFamilies,
    }),
  );
  const { label, apiKey, organization, project, eligibleRouteFamilies } = formState;

  const resetForm = () => {
    setFormState(
      createPlatformIdentityFormState({
        isEdit,
        label: initialLabel,
        organization: initialOrganization,
        project: initialProject,
        eligibleRouteFamilies: initialEligibleRouteFamilies,
      }),
    );
  };

  const hasChanges = useMemo(() => {
    if (!isEdit || !account) {
      return true;
    }
    const nextLabel = label.trim();
    const nextOrganization = normalizeOptionalText(organization);
    const nextProject = normalizeOptionalText(project);
    const nextEligibleRouteFamilies = normalizeEligibleRouteFamilies(eligibleRouteFamilies);
    return (
      nextLabel !== initialLabel ||
      apiKey.trim().length > 0 ||
      nextOrganization !== initialOrganization ||
      nextProject !== initialProject ||
      !areRouteFamiliesEqual(nextEligibleRouteFamilies, initialEligibleRouteFamilies)
    );
  }, [
    account,
    apiKey,
    eligibleRouteFamilies,
    initialEligibleRouteFamilies,
    initialLabel,
    initialOrganization,
    initialProject,
    isEdit,
    label,
    organization,
    project,
  ]);

  const canSubmit = useMemo(
    () =>
      label.trim().length > 0 &&
      (isEdit ? !!account && hasChanges : prerequisiteSatisfied && apiKey.trim().length > 0),
    [account, apiKey, hasChanges, isEdit, label, prerequisiteSatisfied],
  );

  const updateFormState = <Key extends keyof PlatformIdentityFormState>(
    key: Key,
    value: PlatformIdentityFormState[Key],
  ) => {
    setFormState((current) => ({ ...current, [key]: value }));
  };

  const handleRouteToggle = (routeFamily: PlatformRouteFamily, checked: CheckedState) => {
    setFormState((current) => {
      const next = new Set(current.eligibleRouteFamilies);
      if (shouldIncludeRouteFamily(checked)) {
        next.add(routeFamily);
      } else {
        next.delete(routeFamily);
      }
      return {
        ...current,
        eligibleRouteFamilies: PLATFORM_ROUTE_FAMILY_ORDER.filter((value) => next.has(value)),
      };
    });
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    if (isEdit) {
      const payload: PlatformIdentityUpdateRequest = {};
      const nextLabel = label.trim();
      const nextOrganization = normalizeOptionalText(organization);
      const nextProject = normalizeOptionalText(project);
      const nextEligibleRouteFamilies = normalizeEligibleRouteFamilies(eligibleRouteFamilies);

      if (nextLabel !== initialLabel) {
        payload.label = nextLabel;
      }
      if (apiKey.trim().length > 0) {
        payload.apiKey = apiKey.trim();
      }
      if (nextOrganization !== initialOrganization) {
        payload.organization = nextOrganization;
      }
      if (nextProject !== initialProject) {
        payload.project = nextProject;
      }
      if (!areRouteFamiliesEqual(nextEligibleRouteFamilies, initialEligibleRouteFamilies)) {
        payload.eligibleRouteFamilies = nextEligibleRouteFamilies;
      }
      await onSubmit(payload);
    } else {
      await onSubmit({
        label,
        apiKey,
        organization,
        project,
        eligibleRouteFamilies,
      });
    }
    resetForm();
    onOpenChange(false);
  };

  return (
    <form className="space-y-4" onSubmit={handleSubmit}>
      <div className="space-y-2">
        <Label htmlFor="platform-label">Label</Label>
        <Input
          id="platform-label"
          placeholder="Production platform key"
          value={label}
          onChange={(event) => updateFormState("label", event.target.value)}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="platform-api-key">API key</Label>
        <Input
          id="platform-api-key"
          type="password"
          placeholder={isEdit ? "Leave blank to keep the existing key" : "sk-..."}
          value={apiKey}
          onChange={(event) => updateFormState("apiKey", event.target.value)}
        />
        {isEdit ? (
          <p className="text-xs text-muted-foreground">
            Leave blank to keep the current Platform API key. Enter a new key only when rotating credentials.
          </p>
        ) : null}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="platform-organization">Organization</Label>
          <Input
            id="platform-organization"
            placeholder="org_..."
            value={organization}
            onChange={(event) => updateFormState("organization", event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="platform-project">Project</Label>
          <Input
            id="platform-project"
            placeholder="proj_..."
            value={project}
            onChange={(event) => updateFormState("project", event.target.value)}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label>Eligible routes</Label>
        <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
          {PLATFORM_ROUTE_OPTIONS.map((option) => (
            <label key={option.value} className="flex items-start gap-3 rounded-md px-1 py-1.5">
              <Checkbox
                checked={eligibleRouteFamilies.includes(option.value)}
                onCheckedChange={(checked) => handleRouteToggle(option.value, checked)}
              />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{option.label}</span>
                <span className="block text-xs text-muted-foreground">
                  {option.description}
                  {option.value === "public_responses_http"
                    ? " Stateless HTTP only; compact, chat completions, websocket, and continuity-bound requests stay on ChatGPT."
                    : ""}
                </span>
              </span>
            </label>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          {eligibleRouteFamilies.length === 0
            ? "No route families enabled. This identity stays unroutable until you opt into one."
            : `Enabled for ${eligibleRouteFamilies.length} route ${
                eligibleRouteFamilies.length === 1 ? "family" : "families"
              }.`}
        </p>
        <p className="text-xs text-muted-foreground">
          {isEdit
            ? "Only /v1/models and stateless HTTP /v1/responses can ever use this key. ChatGPT-only, compact, websocket, and continuity-bound requests stay on ChatGPT."
            : "Requires an existing ChatGPT account that is not paused or deactivated. Only one Platform API key can be registered, and it is used only for /v1/models plus stateless HTTP /v1/responses fallback."}
        </p>
        {!isEdit && !prerequisiteSatisfied ? (
          <p className="text-xs text-destructive">
            Add or reactivate a ChatGPT account first. Platform keys cannot be used on their own.
          </p>
        ) : null}
      </div>

      {error ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 text-xs text-destructive">
          {error}
        </p>
      ) : null}

      <DialogFooter>
        <Button type="submit" disabled={busy || !canSubmit}>
          {isEdit ? "Save changes" : "Add API key"}
        </Button>
      </DialogFooter>
    </form>
  );
}
