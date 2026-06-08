import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  Bot,
  Gauge,
  KeyRound,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Route,
  Settings2,
  Terminal,
  Trash2,
} from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { listAccounts } from "@/features/accounts/api";
import type { AccountSummary } from "@/features/accounts/schemas";
import {
  useAgentProviderAccounts,
  useAgentProviderOverview,
  useAgentProviderRegistry,
  useAntigravityHarnessPrint,
  useAntigravityManagedInteraction,
  useAgentProviderRouting,
  useCreateAntigravityProviderAccount,
  useCreateGeminiProviderAccount,
  useUpdateAgentProviderAccount,
} from "@/features/agent-providers/hooks/use-agent-providers";
import type {
  AgentProviderAccount,
  AgentProviderAccountUpdate,
} from "@/features/agent-providers/accounts-schemas";
import type {
  AgentProviderPreflight,
  AgentProviderQuotaWindowUpsert,
  AgentProviderRoutingSettings,
  AgentProviderRoutingSettingsUpdate,
  AgentProviderRoutingStrategy,
} from "@/features/agent-providers/routing-schemas";
import type {
  AntigravityHarnessPrintResponse,
  AntigravityManagedInteractionRunResponse,
} from "@/features/agent-providers/harness-schemas";
import type { AgentProviderCapability, AgentProviderOverview } from "@/features/agent-providers/schemas";
import { useSettings } from "@/features/settings/hooks/use-settings";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";
import { getErrorMessageOrNull } from "@/utils/errors";

const STRATEGY_LABELS: Record<AgentProviderRoutingStrategy, string> = {
  capacity_weighted: "Capacity weighted",
  round_robin: "Round robin",
  sequential_drain: "Sequential drain",
  reset_drain: "Reset drain",
  single_account: "Single account",
  ordered_fallback: "Ordered fallback",
};

const STRATEGIES = Object.keys(STRATEGY_LABELS) as AgentProviderRoutingStrategy[];
type CodexRoutingStrategy = DashboardSettings["routingStrategy"];
const CODEX_STRATEGY_LABELS: Record<CodexRoutingStrategy, string> = {
  usage_weighted: "Usage weighted",
  round_robin: "Round robin",
  capacity_weighted: "Capacity weighted",
  relative_availability: "Relative availability",
  fill_first: "Fill first",
  sequential_drain: "Sequential drain",
  reset_drain: "Reset drain",
  single_account: "Single account",
  ordered_fallback: "Ordered fallback",
};
const CODEX_STRATEGIES = Object.keys(CODEX_STRATEGY_LABELS) as CodexRoutingStrategy[];
const COUNT_FORMATTER = new Intl.NumberFormat();

function normalizeProviderAccountOrder(accountIds: readonly string[]): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const rawAccountId of accountIds) {
    const accountId = rawAccountId.trim();
    if (!accountId || seen.has(accountId)) {
      continue;
    }
    seen.add(accountId);
    ordered.push(accountId);
  }
  return ordered;
}

function moveProviderAccountOrder(accountIds: readonly string[], index: number, direction: -1 | 1): string[] {
  const next = [...accountIds];
  const targetIndex = index + direction;
  if (targetIndex < 0 || targetIndex >= next.length) {
    return next;
  }
  [next[index], next[targetIndex]] = [next[targetIndex], next[index]];
  return next;
}

export function ProvidersPage() {
  const providerQuery = useAgentProviderRegistry();
  const overviewQuery = useAgentProviderOverview("7d");
  const codexAccountsQuery = useQuery({
    queryKey: ["accounts", "list", "providers-page"],
    queryFn: listAccounts,
    select: (data) => data.accounts,
  });
  const geminiAccountsQuery = useAgentProviderAccounts("gemini");
  const antigravityAccountsQuery = useAgentProviderAccounts("antigravity");
  const geminiRouting = useAgentProviderRouting("gemini");
  const antigravityRouting = useAgentProviderRouting("antigravity");
  const codexSettings = useSettings();
  const createGeminiAccount = useCreateGeminiProviderAccount();
  const createAntigravityAccount = useCreateAntigravityProviderAccount();
  const updateGeminiAccount = useUpdateAgentProviderAccount("gemini");
  const updateAntigravityAccount = useUpdateAgentProviderAccount("antigravity");
  const antigravityInteraction = useAntigravityManagedInteraction();
  const antigravityHarness = useAntigravityHarnessPrint();

  const providers = providerQuery.data?.providers ?? [];
  const codexProvider = providers.find((provider) => provider.providerId === "codex");
  const geminiProvider = providers.find((provider) => provider.providerId === "gemini");
  const antigravityProvider = providers.find((provider) => provider.providerId === "antigravity");
  const codexAccounts = codexAccountsQuery.data ?? [];
  const geminiAccounts = geminiAccountsQuery.data?.accounts ?? [];
  const antigravityAccounts = antigravityAccountsQuery.data?.accounts ?? [];
  const geminiPreflight = geminiRouting.preflightQuery.data;
  const antigravityPreflight = antigravityRouting.preflightQuery.data;

  const error =
    getErrorMessageOrNull(providerQuery.error) ||
    getErrorMessageOrNull(overviewQuery.error) ||
    getErrorMessageOrNull(codexAccountsQuery.error) ||
    getErrorMessageOrNull(geminiAccountsQuery.error) ||
    getErrorMessageOrNull(antigravityAccountsQuery.error) ||
    getErrorMessageOrNull(codexSettings.settingsQuery.error) ||
    getErrorMessageOrNull(geminiRouting.settingsQuery.error) ||
    getErrorMessageOrNull(geminiRouting.preflightQuery.error) ||
    getErrorMessageOrNull(antigravityRouting.settingsQuery.error) ||
    getErrorMessageOrNull(antigravityRouting.preflightQuery.error) ||
    getErrorMessageOrNull(createGeminiAccount.error) ||
    getErrorMessageOrNull(createAntigravityAccount.error) ||
    getErrorMessageOrNull(updateGeminiAccount.error) ||
    getErrorMessageOrNull(updateAntigravityAccount.error) ||
    getErrorMessageOrNull(codexSettings.updateSettingsMutation.error) ||
    getErrorMessageOrNull(antigravityInteraction.error) ||
    getErrorMessageOrNull(antigravityHarness.error);

  const refresh = () => {
    void providerQuery.refetch();
    void overviewQuery.refetch();
    void codexAccountsQuery.refetch();
    void geminiAccountsQuery.refetch();
    void antigravityAccountsQuery.refetch();
    void codexSettings.settingsQuery.refetch();
    void geminiRouting.settingsQuery.refetch();
    void geminiRouting.preflightQuery.refetch();
    void antigravityRouting.settingsQuery.refetch();
    void antigravityRouting.preflightQuery.refetch();
  };

  const busy =
    providerQuery.isFetching ||
    overviewQuery.isFetching ||
    codexAccountsQuery.isFetching ||
    geminiAccountsQuery.isFetching ||
    antigravityAccountsQuery.isFetching ||
    codexSettings.settingsQuery.isFetching ||
    geminiRouting.settingsQuery.isFetching ||
    geminiRouting.preflightQuery.isFetching ||
    antigravityRouting.settingsQuery.isFetching ||
    antigravityRouting.preflightQuery.isFetching;

  return (
    <div className="animate-fade-in-up space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <Bot className="h-5 w-5 text-primary" />
            Providers
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">Combined provider health and provider-scoped routing.</p>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={refresh} disabled={busy}>
          <RefreshCw className={`h-4 w-4${busy ? " animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

      <CombinedOverview overview={overviewQuery.data} />

      <Tabs defaultValue="gemini" className="space-y-4">
        <TabsList>
          <TabsTrigger value="gemini">Gemini</TabsTrigger>
          <TabsTrigger value="antigravity">Antigravity</TabsTrigger>
          <TabsTrigger value="codex">Codex</TabsTrigger>
        </TabsList>
        <TabsContent value="gemini" className="space-y-4">
          <ProviderHeader
            name={geminiProvider?.displayName ?? "Gemini"}
            status={geminiProvider?.status ?? "foundation"}
            authModes={geminiProvider?.authModes ?? ["api_key"]}
            capabilities={geminiProvider?.capabilities ?? []}
          />
          <GeminiAccountCreateForm
            busy={createGeminiAccount.isPending}
            onSubmit={(payload) => createGeminiAccount.mutateAsync(payload)}
          />
          <ProviderAccountLifecyclePanel
            title="Gemini account lifecycle"
            emptyLabel="No Gemini accounts."
            providerId="gemini"
            accounts={geminiAccounts}
            busy={updateGeminiAccount.isPending}
            onUpdate={(accountId, payload) => updateGeminiAccount.mutateAsync({ accountId, payload })}
          />
          <ProviderRoutingPanel
            title="Gemini routing"
            emptyLabel="No Gemini accounts."
            defaultQuotaDimension="requests_per_day"
            accounts={geminiAccounts}
            settings={geminiRouting.settingsQuery.data}
            preflight={geminiPreflight}
            busy={geminiRouting.updateSettingsMutation.isPending || geminiRouting.upsertQuotaWindowMutation.isPending}
            onSaveSettings={(payload) => geminiRouting.updateSettingsMutation.mutateAsync(payload)}
            onSaveQuota={(accountId, dimension, payload) =>
              geminiRouting.upsertQuotaWindowMutation.mutateAsync({ accountId, dimension, payload })
            }
          />
        </TabsContent>
        <TabsContent value="antigravity" className="space-y-4">
          <ProviderHeader
            name={antigravityProvider?.displayName ?? "Antigravity"}
            status={antigravityProvider?.status ?? "foundation"}
            authModes={antigravityProvider?.authModes ?? ["cli_keyring"]}
            capabilities={antigravityProvider?.capabilities ?? []}
          />
          <AntigravityProfileCreateForm
            busy={createAntigravityAccount.isPending}
            onSubmit={(payload) => createAntigravityAccount.mutateAsync(payload)}
          />
          <ProviderAccountLifecyclePanel
            title="Antigravity profile lifecycle"
            emptyLabel="No Antigravity profiles."
            providerId="antigravity"
            accounts={antigravityAccounts}
            busy={updateAntigravityAccount.isPending}
            onUpdate={(accountId, payload) => updateAntigravityAccount.mutateAsync({ accountId, payload })}
          />
          <ProviderRoutingPanel
            title="Antigravity routing"
            emptyLabel="No Antigravity profiles."
            defaultQuotaDimension="requests"
            accounts={antigravityAccounts}
            settings={antigravityRouting.settingsQuery.data}
            preflight={antigravityPreflight}
            busy={
              antigravityRouting.updateSettingsMutation.isPending ||
              antigravityRouting.upsertQuotaWindowMutation.isPending
            }
            onSaveSettings={(payload) => antigravityRouting.updateSettingsMutation.mutateAsync(payload)}
            onSaveQuota={(accountId, dimension, payload) =>
              antigravityRouting.upsertQuotaWindowMutation.mutateAsync({ accountId, dimension, payload })
            }
          />
          <AntigravityManagedInteractionPanel
            accounts={antigravityAccounts}
            busy={antigravityInteraction.isPending}
            result={antigravityInteraction.data}
            onSubmit={(payload) => antigravityInteraction.mutateAsync(payload)}
          />
          <AntigravityHarnessPanel
            accounts={antigravityAccounts}
            busy={antigravityHarness.isPending}
            result={antigravityHarness.data}
            onSubmit={(payload) => antigravityHarness.mutateAsync(payload)}
          />
        </TabsContent>
        <TabsContent value="codex" className="space-y-4">
          <ProviderHeader
            name={codexProvider?.displayName ?? "Codex"}
            status={codexProvider?.status ?? "ready"}
            authModes={codexProvider?.authModes ?? ["chatgpt_oauth"]}
            capabilities={codexProvider?.capabilities ?? []}
          />
          <CodexPanel
            accounts={codexAccounts}
            settings={codexSettings.settingsQuery.data}
            busy={codexSettings.updateSettingsMutation.isPending}
            onSave={(patch) => {
              const settings = codexSettings.settingsQuery.data;
              if (!settings) {
                return Promise.resolve();
              }
              return codexSettings.updateSettingsMutation.mutateAsync(buildSettingsUpdateRequest(settings, patch));
            }}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function CombinedOverview({ overview }: { overview?: AgentProviderOverview }) {
  const totals = overview?.totals;
  const items = [
    { label: "Providers", value: totals?.providerCount ?? 0, icon: Bot },
    { label: "Accounts", value: totals?.accountCount ?? 0, icon: KeyRound },
    { label: "Active accounts", value: totals?.activeAccountCount ?? 0, icon: Activity },
    { label: "Quota windows", value: totals?.quotaWindowCount ?? 0, icon: Gauge },
    { label: "Requests", value: totals?.requestCount ?? 0, icon: Route },
    { label: "Errors", value: totals?.errorCount ?? 0, icon: Activity },
  ];
  return (
    <section className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        {items.map((item) => (
          <div key={item.label} className="rounded-lg border bg-card p-4">
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm text-muted-foreground">{item.label}</span>
              <item.icon className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="mt-3 text-2xl font-semibold tabular-nums">{formatCount(item.value)}</div>
          </div>
        ))}
      </div>
      {overview?.providers.length ? (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <div className="grid min-w-[42rem] grid-cols-[1.2fr_repeat(5,minmax(4.5rem,1fr))] gap-3 border-b px-4 py-2 text-xs font-medium text-muted-foreground">
            <span>Provider</span>
            <span className="text-right">Accounts</span>
            <span className="text-right">Active</span>
            <span className="text-right">Quota</span>
            <span className="text-right">Requests</span>
            <span className="text-right">Errors</span>
          </div>
          {overview.providers.map((provider) => (
            <div
              key={provider.providerId}
              className="grid min-w-[42rem] grid-cols-[1.2fr_repeat(5,minmax(4.5rem,1fr))] gap-3 border-b px-4 py-3 text-sm last:border-b-0"
            >
              <span className="font-medium">{provider.displayName}</span>
              <span className="text-right tabular-nums">{formatCount(provider.accountCount)}</span>
              <span className="text-right tabular-nums">{formatCount(provider.activeAccountCount)}</span>
              <span className="text-right tabular-nums">{formatCount(provider.quotaWindowCount)}</span>
              <span className="text-right tabular-nums">{formatCount(provider.requestCount)}</span>
              <span className="text-right tabular-nums">{formatCount(provider.errorCount)}</span>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function formatCount(value: number): string {
  return COUNT_FORMATTER.format(value);
}

function ProviderHeader({
  name,
  status,
  authModes,
  capabilities,
}: {
  name: string;
  status: string;
  authModes: string[];
  capabilities: AgentProviderCapability[];
}) {
  return (
    <section className="rounded-lg border bg-card p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-base font-semibold">{name}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{authModes.join(" / ")}</p>
        </div>
        <Badge variant={status === "ready" ? "default" : "secondary"}>{status}</Badge>
      </div>
      {capabilities.length ? (
        <div className="mt-4 grid gap-3 lg:grid-cols-3">
          {capabilities.map((capability) => (
            <div key={capability.protocol} className="rounded-lg border p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium">{capability.protocol}</span>
                <Badge variant={capability.status === "ready" ? "default" : "secondary"}>
                  {capability.status}
                </Badge>
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                <Badge variant={capability.proxyable ? "outline" : "secondary"}>
                  {capability.proxyable ? "Proxy" : "Harness"}
                </Badge>
                <Badge variant={capability.streaming ? "outline" : "secondary"}>
                  {capability.streaming ? "Streaming" : "Session"}
                </Badge>
                {capability.availableUntil ? <Badge variant="secondary">until {capability.availableUntil}</Badge> : null}
              </div>
              <p className="mt-3 text-xs text-muted-foreground">{capability.lifecycleNotes}</p>
              <p className="mt-2 text-xs font-medium">{capability.operatorAction}</p>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function GeminiAccountCreateForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (payload: { displayName: string; apiKey: string; projectId?: string; location?: string }) => Promise<unknown>;
}) {
  const [displayName, setDisplayName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [projectId, setProjectId] = useState("");
  const [location, setLocation] = useState("");

  return (
    <form
      className="rounded-lg border bg-card p-4"
      onSubmit={(event) => {
        event.preventDefault();
        void onSubmit({
          displayName,
          apiKey,
          projectId: projectId || undefined,
          location: location || undefined,
        }).then(() => {
          setDisplayName("");
          setApiKey("");
          setProjectId("");
          setLocation("");
        });
      }}
    >
      <div className="mb-4 flex items-center gap-2">
        <KeyRound className="h-4 w-4 text-primary" />
        <h3 className="text-sm font-semibold">Gemini accounts</h3>
      </div>
      <div className="grid gap-3 lg:grid-cols-[minmax(12rem,1fr)_minmax(16rem,1.3fr)_minmax(10rem,1fr)_minmax(9rem,0.8fr)_auto]">
        <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Display name" required />
        <Input value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="API key" type="password" required />
        <Input value={projectId} onChange={(event) => setProjectId(event.target.value)} placeholder="Project" />
        <Input value={location} onChange={(event) => setLocation(event.target.value)} placeholder="Location" />
        <Button type="submit" disabled={busy}>
          Add
        </Button>
      </div>
    </form>
  );
}

function AntigravityProfileCreateForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (payload: {
    displayName: string;
    authMode?: "api_key" | "cli_keyring";
    apiKey?: string;
    externalAccountId?: string;
    projectId?: string;
    location?: string;
  }) => Promise<unknown>;
}) {
  const [displayName, setDisplayName] = useState("");
  const [authMode, setAuthMode] = useState<"api_key" | "cli_keyring">("api_key");
  const [apiKey, setApiKey] = useState("");
  const [externalAccountId, setExternalAccountId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [location, setLocation] = useState("");

  return (
    <form
      className="rounded-lg border bg-card p-4"
      onSubmit={(event) => {
        event.preventDefault();
        void onSubmit({
          displayName,
          authMode,
          apiKey: authMode === "api_key" ? apiKey : undefined,
          externalAccountId: externalAccountId || undefined,
          projectId: projectId || undefined,
          location: location || undefined,
        }).then(() => {
          setDisplayName("");
          setApiKey("");
          setExternalAccountId("");
          setProjectId("");
          setLocation("");
        });
      }}
    >
      <div className="mb-4 flex items-center gap-2">
        <Bot className="h-4 w-4 text-primary" />
        <h3 className="text-sm font-semibold">Antigravity profiles</h3>
      </div>
      <div className="grid gap-3 lg:grid-cols-[minmax(12rem,1fr)_minmax(10rem,0.8fr)_minmax(14rem,1.2fr)_minmax(10rem,1fr)_minmax(9rem,0.8fr)_auto]">
        <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Display name" required />
        <Select value={authMode} onValueChange={(value) => setAuthMode(value as "api_key" | "cli_keyring")}>
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="api_key">API key</SelectItem>
            <SelectItem value="cli_keyring">CLI profile</SelectItem>
          </SelectContent>
        </Select>
        {authMode === "api_key" ? (
          <Input
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder="Gemini API key"
            type="password"
            required
          />
        ) : (
          <Input
            value={externalAccountId}
            onChange={(event) => setExternalAccountId(event.target.value)}
            placeholder="agy profile id"
            required
          />
        )}
        <Input value={projectId} onChange={(event) => setProjectId(event.target.value)} placeholder="Workspace" />
        <Input value={location} onChange={(event) => setLocation(event.target.value)} placeholder="Harness" />
        <Button type="submit" disabled={busy}>
          Add
        </Button>
      </div>
    </form>
  );
}

function ProviderAccountLifecyclePanel({
  title,
  emptyLabel,
  providerId,
  accounts,
  busy,
  onUpdate,
}: {
  title: string;
  emptyLabel: string;
  providerId: "gemini" | "antigravity";
  accounts: AgentProviderAccount[];
  busy: boolean;
  onUpdate: (accountId: string, payload: AgentProviderAccountUpdate) => Promise<unknown>;
}) {
  if (accounts.length === 0) {
    return <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">{emptyLabel}</p>;
  }
  return (
    <section className="rounded-lg border bg-card p-4">
      <div className="mb-4 flex items-center gap-2">
        <Route className="h-4 w-4 text-primary" aria-hidden="true" />
        <h3 className="text-sm font-semibold">{title}</h3>
      </div>
      <div className="divide-y rounded-lg border">
        {accounts.map((account) => (
          <ProviderAccountLifecycleRow
            key={account.accountId}
            providerId={providerId}
            account={account}
            busy={busy}
            onUpdate={onUpdate}
          />
        ))}
      </div>
    </section>
  );
}

function ProviderAccountLifecycleRow({
  providerId,
  account,
  busy,
  onUpdate,
}: {
  providerId: "gemini" | "antigravity";
  account: AgentProviderAccount;
  busy: boolean;
  onUpdate: (accountId: string, payload: AgentProviderAccountUpdate) => Promise<unknown>;
}) {
  const [displayName, setDisplayName] = useState(account.displayName);
  const [externalAccountId, setExternalAccountId] = useState(account.externalAccountId ?? "");
  const [projectId, setProjectId] = useState(account.projectId ?? "");
  const [location, setLocation] = useState(account.location ?? "");
  const [apiKey, setApiKey] = useState("");
  const nextStatus = account.status === "paused" ? "active" : "paused";
  const canEditExternalId = providerId === "antigravity";
  const canRotateApiKey = providerId === "gemini" || account.authMode === "api_key";

  return (
    <div className="grid gap-3 p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-medium">{account.displayName}</span>
            <Badge variant={account.status === "active" ? "default" : "secondary"}>{account.status}</Badge>
            {account.apiKeySet ? <Badge variant="outline">key set</Badge> : null}
          </div>
          <p className="mt-1 truncate text-xs text-muted-foreground">
            {account.externalAccountId ?? account.credentialFingerprint ?? account.accountId}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => void onUpdate(account.accountId, { status: nextStatus })}
        >
          {nextStatus === "active" ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
          {nextStatus === "active" ? "Resume" : "Pause"}
        </Button>
      </div>

      <form
        className="grid gap-3 lg:grid-cols-[minmax(11rem,1fr)_minmax(10rem,1fr)_minmax(10rem,1fr)_minmax(9rem,0.8fr)_auto]"
        onSubmit={(event) => {
          event.preventDefault();
          void onUpdate(account.accountId, {
            displayName,
            externalAccountId: canEditExternalId ? externalAccountId : undefined,
            projectId: projectId || null,
            location: location || null,
          });
        }}
      >
        <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required />
        {canEditExternalId ? (
          <Input
            value={externalAccountId}
            onChange={(event) => setExternalAccountId(event.target.value)}
            placeholder={account.authMode === "api_key" ? "Agent id" : "Profile id"}
            required={account.authMode === "cli_keyring"}
          />
        ) : (
          null
        )}
        <Input value={projectId} onChange={(event) => setProjectId(event.target.value)} placeholder="Project" />
        <Input
          value={location}
          onChange={(event) => setLocation(event.target.value)}
          placeholder={canEditExternalId ? "Harness" : "Location"}
        />
        <Button type="submit" variant="outline" disabled={busy}>
          <Settings2 className="h-4 w-4" />
          Save
        </Button>
      </form>

      {canRotateApiKey ? (
        <form
          className="grid gap-3 sm:grid-cols-[minmax(14rem,1fr)_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            void onUpdate(account.accountId, { apiKey }).then(() => setApiKey(""));
          }}
        >
          <Input
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder="New API key"
            type="password"
            required
          />
          <Button type="submit" variant="outline" disabled={busy || !apiKey}>
            <RefreshCw className="h-4 w-4" />
            Rotate
          </Button>
        </form>
      ) : null}
    </div>
  );
}

function AntigravityManagedInteractionPanel({
  accounts,
  busy,
  result,
  onSubmit,
}: {
  accounts: AgentProviderAccount[];
  busy: boolean;
  result?: AntigravityManagedInteractionRunResponse;
  onSubmit: (payload: { agent?: string; input: string; environment?: string }) => Promise<unknown>;
}) {
  const [agent, setAgent] = useState("antigravity-preview-05-2026");
  const [input, setInput] = useState("");
  const [environment, setEnvironment] = useState("remote");
  const apiAccounts = accounts.filter((account) => account.authMode === "api_key");
  const disabled = busy || apiAccounts.length === 0;

  return (
    <section className="rounded-lg border bg-card p-4">
      <div className="mb-4 flex items-center gap-2">
        <Bot className="h-4 w-4 text-primary" />
        <h3 className="text-sm font-semibold">Antigravity managed agent</h3>
      </div>
      <form
        className="grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void onSubmit({ agent, input, environment });
        }}
      >
        <div className="grid gap-3 lg:grid-cols-[minmax(14rem,1fr)_minmax(9rem,0.55fr)_auto]">
          <Input value={agent} onChange={(event) => setAgent(event.target.value)} required />
          <Input value={environment} onChange={(event) => setEnvironment(event.target.value)} required />
          <Button type="submit" disabled={disabled || !input}>
            <Play className="h-4 w-4" />
            Run
          </Button>
        </div>
        <textarea
          className="min-h-28 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none ring-offset-background placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Input"
          required
        />
        <span className="text-sm text-muted-foreground">
          {apiAccounts.length
            ? `${apiAccounts.length} API key account${apiAccounts.length === 1 ? "" : "s"} available`
            : "No API key accounts"}
        </span>
      </form>
      {result ? (
        <div className="mt-4 overflow-hidden rounded-lg border">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-muted/40 px-3 py-2 text-xs">
            <span className="font-medium">{result.agent}</span>
            <span className="text-muted-foreground">Interactions API</span>
          </div>
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap p-3 text-xs">
            {result.outputText || JSON.stringify(result.response, null, 2)}
          </pre>
        </div>
      ) : null}
    </section>
  );
}

function AntigravityHarnessPanel({
  accounts,
  busy,
  result,
  onSubmit,
}: {
  accounts: AgentProviderAccount[];
  busy: boolean;
  result?: AntigravityHarnessPrintResponse;
  onSubmit: (payload: {
    prompt: string;
    workspacePath: string;
    timeoutSeconds: number;
    continueConversation?: boolean;
  }) => Promise<unknown>;
}) {
  const [prompt, setPrompt] = useState("");
  const [workspacePath, setWorkspacePath] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("300");
  const [continueConversation, setContinueConversation] = useState(false);
  const disabled = busy || accounts.length === 0;

  return (
    <section className="rounded-lg border bg-card p-4">
      <div className="mb-4 flex items-center gap-2">
        <Terminal className="h-4 w-4 text-primary" />
        <h3 className="text-sm font-semibold">Antigravity harness</h3>
      </div>
      <form
        className="grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void onSubmit({
            prompt,
            workspacePath,
            timeoutSeconds: Number(timeoutSeconds),
            continueConversation,
          });
        }}
      >
        <div className="grid gap-3 lg:grid-cols-[minmax(16rem,1fr)_7rem_auto]">
          <Input
            value={workspacePath}
            onChange={(event) => setWorkspacePath(event.target.value)}
            placeholder="Workspace path"
            required
          />
          <Input
            value={timeoutSeconds}
            onChange={(event) => setTimeoutSeconds(event.target.value)}
            type="number"
            min={1}
            max={1800}
          />
          <label className="flex h-10 items-center gap-2 rounded-md border px-3 text-sm">
            <input
              type="checkbox"
              checked={continueConversation}
              onChange={(event) => setContinueConversation(event.target.checked)}
            />
            Continue
          </label>
        </div>
        <textarea
          className="min-h-28 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none ring-offset-background placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="Prompt"
          required
        />
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-muted-foreground">
            {accounts.length ? `${accounts.length} profile${accounts.length === 1 ? "" : "s"} available` : "No profiles"}
          </span>
          <Button type="submit" disabled={disabled}>
            <Play className="h-4 w-4" />
            Run
          </Button>
        </div>
      </form>
      {result ? (
        <div className="mt-4 overflow-hidden rounded-lg border">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-muted/40 px-3 py-2 text-xs">
            <span className="font-medium">{result.externalAccountId ?? result.accountId}</span>
            <span className="text-muted-foreground">
              exit {result.exitCode} / {result.durationMs}ms
            </span>
          </div>
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap p-3 text-xs">{result.stdout || result.stderr}</pre>
        </div>
      ) : null}
    </section>
  );
}

function ProviderRoutingPanel({
  title,
  emptyLabel,
  defaultQuotaDimension,
  accounts,
  settings,
  preflight,
  busy,
  onSaveSettings,
  onSaveQuota,
}: {
  title: string;
  emptyLabel: string;
  defaultQuotaDimension: string;
  accounts: AgentProviderAccount[];
  settings?: AgentProviderRoutingSettings;
  preflight?: AgentProviderPreflight;
  busy: boolean;
  onSaveSettings: (payload: AgentProviderRoutingSettingsUpdate) => Promise<unknown>;
  onSaveQuota: (accountId: string, dimension: string, payload: AgentProviderQuotaWindowUpsert) => Promise<unknown>;
}) {
  const [strategyDraft, setStrategyDraft] = useState<AgentProviderRoutingStrategy | null>(null);
  const [singleAccountDraft, setSingleAccountDraft] = useState<string | null>(null);
  const [orderedAccountDraft, setOrderedAccountDraft] = useState<string[] | null>(null);
  const [thresholdDraft, setThresholdDraft] = useState<string | null>(null);
  const [quotaAccountDraft, setQuotaAccountDraft] = useState("");
  const [dimension, setDimension] = useState(defaultQuotaDimension);
  const [used, setUsed] = useState("0");
  const [limit, setLimit] = useState("100");
  const strategy = strategyDraft ?? settings?.strategy ?? "capacity_weighted";
  const singleAccountId = singleAccountDraft ?? settings?.singleAccountId ?? "";
  const orderedAccountIds = orderedAccountDraft ?? normalizeProviderAccountOrder(settings?.orderedAccountIds ?? []);
  const orderedAccountIdSet = new Set(orderedAccountIds);
  const nextOrderedAccountId = accounts.find((account) => !orderedAccountIdSet.has(account.accountId))?.accountId;
  const threshold = thresholdDraft ?? String(settings?.quotaThresholdPct ?? 100);
  const quotaAccountId = quotaAccountDraft || accounts[0]?.accountId || "";

  return (
    <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(20rem,0.7fr)]">
      <div className="space-y-4 rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2">
          <Settings2 className="h-4 w-4 text-primary" />
          <h3 className="text-sm font-semibold">{title}</h3>
        </div>
        <form
          className="grid gap-3 md:grid-cols-[minmax(12rem,1fr)_minmax(12rem,1fr)_8rem_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            void onSaveSettings({
              strategy,
              singleAccountId: singleAccountId || null,
              orderedAccountIds:
                strategy === "ordered_fallback" && orderedAccountIds.length === 0 && accounts[0]
                  ? [accounts[0].accountId]
                  : orderedAccountIds,
              quotaThresholdPct: Number(threshold),
            });
          }}
        >
          <Select
            value={strategy}
            onValueChange={(value) => {
              const nextStrategy = value as AgentProviderRoutingStrategy;
              setStrategyDraft(nextStrategy);
              if (nextStrategy === "ordered_fallback" && orderedAccountIds.length === 0 && accounts[0]) {
                setOrderedAccountDraft([accounts[0].accountId]);
              }
            }}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STRATEGIES.map((option) => (
                <SelectItem key={option} value={option}>
                  {STRATEGY_LABELS[option]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={singleAccountId || "none"} onValueChange={(value) => setSingleAccountDraft(value === "none" ? "" : value)}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">No single account</SelectItem>
              {accounts.map((account) => (
                <SelectItem key={account.accountId} value={account.accountId}>
                  {account.displayName}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input value={threshold} onChange={(event) => setThresholdDraft(event.target.value)} type="number" min={0} max={100} />
          <Button type="submit" disabled={busy || (strategy === "ordered_fallback" && orderedAccountIds.length === 0)}>
            Save
          </Button>
        </form>

        {strategy === "ordered_fallback" ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs font-medium text-muted-foreground">Account priority</p>
              <Button
                type="button"
                size="icon"
                variant="outline"
                className="h-8 w-8"
                disabled={busy || !nextOrderedAccountId}
                aria-label="Add provider account priority"
                onClick={() =>
                  nextOrderedAccountId ? setOrderedAccountDraft([...orderedAccountIds, nextOrderedAccountId]) : undefined
                }
              >
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
            </div>
            {orderedAccountIds.map((accountId, index) => {
              const account = accounts.find((item) => item.accountId === accountId);
              const availableAccounts = accounts.filter(
                (item) => item.accountId === accountId || !orderedAccountIdSet.has(item.accountId),
              );
              return (
                <div key={accountId + "-" + index} className="flex items-center gap-2">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border bg-muted/20 text-xs text-muted-foreground">
                    {index + 1}
                  </div>
                  <Select
                    value={accountId}
                    onValueChange={(value) => {
                      const next = [...orderedAccountIds];
                      next[index] = value;
                      setOrderedAccountDraft(normalizeProviderAccountOrder(next));
                    }}
                  >
                    <SelectTrigger className="h-8 min-w-0 flex-1 text-xs" disabled={busy}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {availableAccounts.map((item) => (
                        <SelectItem key={item.accountId} value={item.accountId}>
                          {item.displayName}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="h-8 w-8"
                    disabled={busy || index === 0}
                    aria-label={"Move " + (account?.displayName ?? accountId) + " up"}
                    onClick={() => setOrderedAccountDraft(moveProviderAccountOrder(orderedAccountIds, index, -1))}
                  >
                    <ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
                  </Button>
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="h-8 w-8"
                    disabled={busy || index === orderedAccountIds.length - 1}
                    aria-label={"Move " + (account?.displayName ?? accountId) + " down"}
                    onClick={() => setOrderedAccountDraft(moveProviderAccountOrder(orderedAccountIds, index, 1))}
                  >
                    <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
                  </Button>
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="h-8 w-8"
                    disabled={busy || orderedAccountIds.length <= 1}
                    aria-label={"Remove " + (account?.displayName ?? accountId)}
                    onClick={() =>
                      setOrderedAccountDraft(orderedAccountIds.filter((_, itemIndex) => itemIndex !== index))
                    }
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  </Button>
                </div>
              );
            })}
          </div>
        ) : null}

        <form
          className="grid gap-3 md:grid-cols-[minmax(12rem,1fr)_minmax(10rem,1fr)_7rem_7rem_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            if (!quotaAccountId) return;
            void onSaveQuota(quotaAccountId, dimension, {
              dimension,
              used: Number(used),
              limit: limit ? Number(limit) : null,
            });
          }}
        >
          <Select value={quotaAccountId || "none"} onValueChange={(value) => setQuotaAccountDraft(value === "none" ? "" : value)}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">No account</SelectItem>
              {accounts.map((account) => (
                <SelectItem key={account.accountId} value={account.accountId}>
                  {account.displayName}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input value={dimension} onChange={(event) => setDimension(event.target.value)} />
          <Input value={used} onChange={(event) => setUsed(event.target.value)} type="number" min={0} />
          <Input value={limit} onChange={(event) => setLimit(event.target.value)} type="number" min={0} />
          <Button type="submit" variant="outline" disabled={busy || !quotaAccountId}>
            Set quota
          </Button>
        </form>

        <ProviderAccountTable accounts={preflight?.accounts ?? []} emptyLabel={emptyLabel} />
      </div>

      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2">
          <Route className="h-4 w-4 text-primary" />
          <h3 className="text-sm font-semibold">Preflight</h3>
        </div>
        <dl className="mt-4 space-y-3 text-sm">
          <MetricRow label="Selected" value={preflight?.selectedAccountId ?? "None"} />
          <MetricRow label="Denied" value={preflight?.deniedReason ?? "None"} />
          <MetricRow label="Candidates" value={String(preflight?.candidateAccountIds.length ?? 0)} />
        </dl>
      </div>
    </section>
  );
}

function ProviderAccountTable({
  accounts,
  emptyLabel,
}: {
  accounts: AgentProviderPreflight["accounts"];
  emptyLabel: string;
}) {
  if (accounts.length === 0) {
    return <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">{emptyLabel}</p>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full min-w-[34rem] text-sm">
        <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Account</th>
            <th className="px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium">Quota</th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((account) => (
            <tr key={account.accountId} className="border-t">
              <td className="px-3 py-2 font-medium">{account.displayName}</td>
              <td className="px-3 py-2">{account.status}</td>
              <td className="px-3 py-2 text-muted-foreground">
                {account.quotaWindows.length
                  ? account.quotaWindows.map((window) => `${window.dimension}: ${window.used}/${window.limit ?? "open"}`).join(", ")
                  : "No windows"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CodexPanel({
  accounts,
  settings,
  busy,
  onSave,
}: {
  accounts: AccountSummary[];
  settings?: DashboardSettings;
  busy: boolean;
  onSave: (patch: Partial<SettingsUpdateRequest>) => Promise<unknown>;
}) {
  const active = accounts.filter((account) => account.status === "active").length;
  const limited = accounts.length - active;
  return (
    <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(18rem,0.55fr)]">
      <div className="rounded-lg border bg-card p-4">
        <div className="mb-4 flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-primary" />
          <h3 className="text-sm font-semibold">Codex accounts</h3>
        </div>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full min-w-[34rem] text-sm">
            <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Account</th>
                <th className="px-3 py-2 font-medium">Plan</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Primary</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => (
                <tr key={account.accountId} className="border-t">
                  <td className="px-3 py-2 font-medium">{account.displayName || account.email}</td>
                  <td className="px-3 py-2">{account.planType}</td>
                  <td className="px-3 py-2">{account.status}</td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {account.usage?.primaryRemainingPercent == null
                      ? "Unknown"
                      : `${account.usage.primaryRemainingPercent.toFixed(1)}% remaining`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2">
          <Gauge className="h-4 w-4 text-primary" />
          <h3 className="text-sm font-semibold">Codex routing</h3>
        </div>
        <dl className="mt-4 space-y-3 text-sm">
          <MetricRow label="Total" value={String(accounts.length)} />
          <MetricRow label="Active" value={String(active)} />
          <MetricRow label="Limited" value={String(limited)} />
          <MetricRow label="Strategy" value={settings ? CODEX_STRATEGY_LABELS[settings.routingStrategy] : "Loading"} />
          <MetricRow
            label="Primary gate"
            value={
              settings?.stickyReallocationPrimaryBudgetThresholdPct == null
                ? "Unset"
                : `${settings.stickyReallocationPrimaryBudgetThresholdPct}%`
            }
          />
        </dl>
      </div>
      <CodexRoutingPanel accounts={accounts} settings={settings} busy={busy} onSave={onSave} />
    </section>
  );
}

function CodexRoutingPanel({
  accounts,
  settings,
  busy,
  onSave,
}: {
  accounts: AccountSummary[];
  settings?: DashboardSettings;
  busy: boolean;
  onSave: (patch: Partial<SettingsUpdateRequest>) => Promise<unknown>;
}) {
  const [thresholdDraft, setThresholdDraft] = useState("");
  const selectableAccounts = accounts.filter((account) =>
    ["active", "rate_limited", "quota_exceeded"].includes(account.status),
  );
  const firstAccountId = selectableAccounts[0]?.accountId;
  const manualAccountIds = normalizeProviderAccountOrder(settings?.manualAccountPriorityIds ?? []);
  const manualAccountIdSet = new Set(manualAccountIds);
  const nextManualAccountId = selectableAccounts.find((account) => !manualAccountIdSet.has(account.accountId))?.accountId;
  const thresholdValue =
    thresholdDraft ||
    (settings?.stickyReallocationPrimaryBudgetThresholdPct == null
      ? ""
      : String(settings.stickyReallocationPrimaryBudgetThresholdPct));

  if (!settings) {
    return <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">Codex settings loading.</p>;
  }

  const saveStrategy = (strategy: CodexRoutingStrategy) => {
    if (strategy === "single_account") {
      const selectedAccountId = settings.singleAccountId ?? firstAccountId;
      if (!selectedAccountId) return;
      void onSave({ routingStrategy: strategy, singleAccountId: selectedAccountId });
      return;
    }
    if (strategy === "ordered_fallback") {
      const orderedIds = manualAccountIds.length > 0 ? manualAccountIds : firstAccountId ? [firstAccountId] : [];
      if (orderedIds.length === 0) return;
      void onSave({ routingStrategy: strategy, manualAccountPriorityIds: orderedIds });
      return;
    }
    void onSave({ routingStrategy: strategy });
  };

  const saveManualAccountIds = (accountIds: string[]) => {
    void onSave({ manualAccountPriorityIds: normalizeProviderAccountOrder(accountIds) });
  };

  return (
    <div className="rounded-lg border bg-card p-4 xl:col-span-2">
      <div className="mb-4 flex items-center gap-2">
        <Settings2 className="h-4 w-4 text-primary" />
        <h3 className="text-sm font-semibold">Codex settings</h3>
      </div>
      <div className="grid gap-3 md:grid-cols-[minmax(12rem,1fr)_minmax(12rem,1fr)_8rem_auto]">
        <Select value={settings.routingStrategy} onValueChange={(value) => saveStrategy(value as CodexRoutingStrategy)}>
          <SelectTrigger className="w-full" disabled={busy}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {CODEX_STRATEGIES.map((option) => (
              <SelectItem
                key={option}
                value={option}
                disabled={
                  (option === "single_account" && !settings.singleAccountId && !firstAccountId) ||
                  (option === "ordered_fallback" && manualAccountIds.length === 0 && !firstAccountId)
                }
              >
                {CODEX_STRATEGY_LABELS[option]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={settings.singleAccountId ?? "none"}
          onValueChange={(value) => void onSave({ singleAccountId: value === "none" ? null : value })}
        >
          <SelectTrigger className="w-full" disabled={busy || selectableAccounts.length === 0}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">No single account</SelectItem>
            {selectableAccounts.map((account) => (
              <SelectItem key={account.accountId} value={account.accountId}>
                {accountLabel(account)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          value={thresholdValue}
          onChange={(event) => setThresholdDraft(event.target.value)}
          type="number"
          min={0}
          max={100}
          disabled={busy}
          aria-label="Codex primary threshold"
        />
        <Button
          type="button"
          variant="outline"
          disabled={busy || thresholdValue === ""}
          onClick={() => {
            const threshold = Number(thresholdValue);
            void onSave({
              stickyReallocationPrimaryBudgetThresholdPct: threshold,
              stickyReallocationSecondaryBudgetThresholdPct:
                settings.stickyReallocationSecondaryBudgetThresholdPct ?? 100,
            }).then(() => setThresholdDraft(""));
          }}
        >
          Save
        </Button>
      </div>

      {settings.routingStrategy === "ordered_fallback" ? (
        <div className="mt-4 space-y-2">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs font-medium text-muted-foreground">Account priority</p>
            <Button
              type="button"
              size="icon"
              variant="outline"
              className="h-8 w-8"
              disabled={busy || !nextManualAccountId}
              aria-label="Add Codex account priority"
              onClick={() => (nextManualAccountId ? saveManualAccountIds([...manualAccountIds, nextManualAccountId]) : undefined)}
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          </div>
          {manualAccountIds.map((accountId, index) => {
            const account = accounts.find((item) => item.accountId === accountId);
            const availableAccounts = selectableAccounts.filter(
              (item) => item.accountId === accountId || !manualAccountIdSet.has(item.accountId),
            );
            return (
              <div key={`${accountId}-${index}`} className="flex items-center gap-2">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border bg-muted/20 text-xs text-muted-foreground">
                  {index + 1}
                </div>
                <Select
                  value={accountId}
                  onValueChange={(value) => {
                    const next = [...manualAccountIds];
                    next[index] = value;
                    saveManualAccountIds(next);
                  }}
                >
                  <SelectTrigger className="h-8 min-w-0 flex-1 text-xs" disabled={busy}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {availableAccounts.map((item) => (
                      <SelectItem key={item.accountId} value={item.accountId}>
                        {accountLabel(item)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8"
                  disabled={busy || index === 0}
                  aria-label={"Move " + (account?.displayName ?? accountId) + " up"}
                  onClick={() => saveManualAccountIds(moveProviderAccountOrder(manualAccountIds, index, -1))}
                >
                  <ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8"
                  disabled={busy || index === manualAccountIds.length - 1}
                  aria-label={"Move " + (account?.displayName ?? accountId) + " down"}
                  onClick={() => saveManualAccountIds(moveProviderAccountOrder(manualAccountIds, index, 1))}
                >
                  <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8"
                  disabled={busy || manualAccountIds.length <= 1}
                  aria-label={"Remove " + (account?.displayName ?? accountId)}
                  onClick={() => saveManualAccountIds(manualAccountIds.filter((_, itemIndex) => itemIndex !== index))}
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function accountLabel(account: AccountSummary): string {
  return account.displayName || account.email || account.accountId;
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="max-w-[12rem] truncate font-medium tabular-nums">{value}</dd>
    </div>
  );
}
