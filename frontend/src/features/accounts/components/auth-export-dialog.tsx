import { useState } from "react";
import { Download } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { CopyButton } from "@/components/copy-button";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AccountAuthExportResponse } from "@/features/accounts/schemas";
import { usePrivacyStore } from "@/hooks/use-privacy";

type AuthFormat = "codex" | "opencode";

export type AuthExportDialogProps = {
  open: boolean;
  exportData: AccountAuthExportResponse | null;
  onOpenChange: (open: boolean) => void;
};

function truncateSecret(value: string, leading = 18, trailing = 10): string {
  if (value.length <= leading + trailing + 1) return value;
  return `${value.slice(0, leading)}…${value.slice(-trailing)}`;
}

function codexAuthString(exportData: AccountAuthExportResponse): string {
  return `${JSON.stringify(exportData.codexAuthJson, null, 2)}\n`;
}

function codexAuthPreview(exportData: AccountAuthExportResponse): string {
  return `${JSON.stringify(
    {
      auth_mode: exportData.codexAuthJson.auth_mode,
      OPENAI_API_KEY: exportData.codexAuthJson.OPENAI_API_KEY,
      tokens: {
        id_token: truncateSecret(exportData.codexAuthJson.tokens.id_token),
        access_token: truncateSecret(exportData.codexAuthJson.tokens.access_token),
        refresh_token: truncateSecret(exportData.codexAuthJson.tokens.refresh_token),
        account_id: exportData.codexAuthJson.tokens.account_id,
      },
      last_refresh: exportData.codexAuthJson.last_refresh,
    },
    null,
    2,
  )}\n`;
}

function opencodeAuthPreview(exportData: AccountAuthExportResponse): string {
  return `${JSON.stringify(
    {
      openai: {
        ...exportData.opencodeAuthJson.openai,
        access: truncateSecret(exportData.opencodeAuthJson.openai.access),
        refresh: truncateSecret(exportData.opencodeAuthJson.openai.refresh),
      },
    },
    null,
    2,
  )}\n`;
}

function downloadAuthJson(exportData: AccountAuthExportResponse, format: AuthFormat): void {
  const content = format === "codex" ? codexAuthString(exportData) : `${JSON.stringify(exportData.opencodeAuthJson, null, 2)}\n`;
  const blob = new Blob([content], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = format === "codex" ? "auth.json" : exportData.filename;
  document.body.append(link);
  try {
    link.click();
  } finally {
    link.remove();
    URL.revokeObjectURL(url);
  }
}

function AuthExportDialogBody({
  exportData,
  onOpenChange,
}: {
  exportData: AccountAuthExportResponse;
  onOpenChange: (open: boolean) => void;
}) {
  const blurred = usePrivacyStore((s) => s.blurred);
  const [format, setFormat] = useState<AuthFormat>("codex");

  const authPreview = format === "codex" ? codexAuthPreview(exportData) : opencodeAuthPreview(exportData);

  const authJson =
    format === "codex" ? codexAuthString(exportData) : `${JSON.stringify(exportData.opencodeAuthJson, null, 2)}\n`;

  const tokenPreviewRows =
    format === "codex"
      ? [
          {
            label: "ID token",
            value: exportData.codexAuthJson.tokens.id_token,
            copyLabel: "Copy ID token",
          },
          {
            label: "Access token",
            value: exportData.codexAuthJson.tokens.access_token,
            copyLabel: "Copy access token",
          },
          {
            label: "Refresh token",
            value: exportData.codexAuthJson.tokens.refresh_token,
            copyLabel: "Copy refresh token",
          },
        ]
      : [
          {
            label: "Access token",
            value: exportData.opencodeAuthJson.openai.access,
            copyLabel: "Copy access token",
          },
          {
            label: "Refresh token",
            value: exportData.opencodeAuthJson.openai.refresh,
            copyLabel: "Copy refresh token",
          },
        ];

  return (
    <>
      <div className="space-y-4">
        <AlertMessage variant="warning">
          This payload contains raw access and refresh tokens. Store it only on machines you trust.
        </AlertMessage>

        <div className="rounded-lg border bg-muted/20 p-3 text-xs">
          <div className="font-medium">Exported account</div>
          <div className="mt-1 text-muted-foreground">
            <span className={blurred ? "privacy-blur" : undefined}>{exportData.account.email}</span>
          </div>
          <div className="mt-1 font-mono text-muted-foreground">
            {exportData.account.chatgptAccountId ?? exportData.account.accountId}
          </div>
        </div>

        <div className="space-y-2">
          <Label id="auth-export-format-label">Format</Label>
          <Select value={format} onValueChange={(v) => setFormat(v as AuthFormat)}>
            <SelectTrigger className="w-[180px]" aria-labelledby="auth-export-format-label">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="codex">codex</SelectItem>
              <SelectItem value="opencode">opencode</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <div>
            <div className="text-sm font-medium">Token preview</div>
            <div className="text-xs text-muted-foreground">
              Truncated on screen for readability. Copy buttons still use the full token.
            </div>
          </div>

          <div className="overflow-hidden rounded-lg border bg-muted/20">
            {tokenPreviewRows.map((row, index) => (
              <div
                key={row.copyLabel}
                className={`flex items-center justify-between gap-3 px-3 py-2 ${
                  index < tokenPreviewRows.length - 1 ? "border-b" : ""
                }`}
              >
                <div className="min-w-0">
                  <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    {row.label}
                  </div>
                  <div className="truncate font-mono text-xs">{truncateSecret(row.value)}</div>
                </div>
                <CopyButton value={row.value} label={row.copyLabel} iconOnly />
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-medium">auth.json</div>
            <CopyButton value={authJson} label="Copy auth.json" />
          </div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg border bg-muted/20 p-3 text-xs">
            {authPreview}
          </pre>
        </div>
      </div>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
          Close
        </Button>
        <Button type="button" className="gap-1.5" onClick={() => downloadAuthJson(exportData, format)}>
          <Download className="h-4 w-4" />
          Download
        </Button>
      </DialogFooter>
    </>
  );
}

export function AuthExportDialog({
  open,
  exportData,
  onOpenChange,
}: AuthExportDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Auth Export</DialogTitle>
          <DialogDescription>
            Download or copy this account as an auth.json file.
          </DialogDescription>
        </DialogHeader>

        {exportData ? (
          <AuthExportDialogBody
            key={open ? "open" : "closed"}
            exportData={exportData}
            onOpenChange={onOpenChange}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
