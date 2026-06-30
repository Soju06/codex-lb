import { Switch } from "@/components/ui/switch";

export type ApiKeyQuotaPrivacyToggleProps = {
  enabled: boolean;
  disabled?: boolean;
  onChange: (enabled: boolean) => void;
};

export function ApiKeyQuotaPrivacyToggle({
  enabled,
  disabled = false,
  onChange,
}: ApiKeyQuotaPrivacyToggleProps) {
  return (
    <div className="flex items-center justify-between rounded-lg border p-3">
      <div className="space-y-1">
        <p className="text-sm font-medium">Hide upstream quota</p>
        <p className="text-xs text-muted-foreground">API-key clients only see the key's own quota and usage.</p>
      </div>
      <Switch checked={enabled} disabled={disabled} onCheckedChange={onChange} />
    </div>
  );
}
