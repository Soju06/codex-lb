import { Switch } from "@/components/ui/switch";

export type ApiKeyAuthToggleProps = {
  enabled: boolean;
  disabled?: boolean;
  onChange: (enabled: boolean) => void;
};

export function ApiKeyAuthToggle({ enabled, disabled = false, onChange }: ApiKeyAuthToggleProps) {
  return (
    <div className="flex items-center justify-between rounded-lg border p-3">
      <div className="space-y-1">
        <p className="text-sm font-medium">API Key Auth</p>
        <p className="text-xs text-muted-foreground">
          Require API keys for `/v1/*`, `/backend-api/codex/*`, and `/backend-api/transcribe`. When disabled,
          only local proxy requests can proceed without a key.
        </p>
      </div>
      <Switch checked={enabled} disabled={disabled} onCheckedChange={onChange} />
    </div>
  );
}
