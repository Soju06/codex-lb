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
import type { AccountOpenCodeAuthExportResponse } from "@/features/accounts/schemas";

export type OpenCodeAuthExportDialogProps = {
  open: boolean;
  exportData: AccountOpenCodeAuthExportResponse | null;
  onOpenChange: (open: boolean) => void;
};

function stringifyAuthJson(exportData: AccountOpenCodeAuthExportResponse): string {
  return `${JSON.stringify(exportData.authJson, null, 2)}\n`;
}

function truncateSecret(value: string, leading = 18, trailing = 10): string {
  if (value.length <= leading + trailing + 1) return value;
  return `${value.slice(0, leading)}…${value.slice(-trailing)}`;
}

function stringifyAuthPreview(exportData: AccountOpenCodeAuthExportResponse): string {
  return `${JSON.stringify(
    {
      openai: {
        ...exportData.authJson.openai,
        access: truncateSecret(exportData.authJson.openai.access),
        refresh: truncateSecret(exportData.authJson.openai.refresh),
      },
    },
    null,
    2,
  )}\n`;
}

function downloadAuthJson(exportData: AccountOpenCodeAuthExportResponse): void {
  const blob = new Blob([stringifyAuthJson(exportData)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = exportData.filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function OpenCodeAuthExportDialog({
  open,
  exportData,
  onOpenChange,
}: OpenCodeAuthExportDialogProps) {
  const authJson = exportData ? stringifyAuthJson(exportData) : "";
  const authPreview = exportData ? stringifyAuthPreview(exportData) : "";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>OpenCode auth export</DialogTitle>
          <DialogDescription>
            Download or copy this account as a stock OpenCode auth.json file.
          </DialogDescription>
        </DialogHeader>

        {exportData ? (
          <div className="space-y-4">
            <AlertMessage variant="warning">
              This payload contains raw access and refresh tokens. Store it only on machines you trust.
            </AlertMessage>

            <div className="rounded-lg border bg-muted/20 p-3 text-xs">
              <div className="font-medium">Exported account</div>
              <div className="mt-1 text-muted-foreground">{exportData.account.email}</div>
              <div className="mt-1 font-mono text-muted-foreground">
                {exportData.account.chatgptAccountId ?? exportData.account.accountId}
              </div>
            </div>

            <div className="space-y-2">
              <div>
                <div className="text-sm font-medium">Token preview</div>
                <div className="text-xs text-muted-foreground">
                  Truncated on screen for readability. Copy buttons still use the full token.
                </div>
              </div>

              <div className="overflow-hidden rounded-lg border bg-muted/20">
                <div className="flex items-center justify-between gap-3 border-b px-3 py-2">
                  <div className="min-w-0">
                    <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      Access token
                    </div>
                    <div className="truncate font-mono text-xs">{truncateSecret(exportData.authJson.openai.access)}</div>
                  </div>
                  <CopyButton value={exportData.authJson.openai.access} label="Copy access token" iconOnly />
                </div>
                <div className="flex items-center justify-between gap-3 px-3 py-2">
                  <div className="min-w-0">
                    <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      Refresh token
                    </div>
                    <div className="truncate font-mono text-xs">{truncateSecret(exportData.authJson.openai.refresh)}</div>
                  </div>
                  <CopyButton value={exportData.authJson.openai.refresh} label="Copy refresh token" iconOnly />
                </div>
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
        ) : null}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button
            type="button"
            className="gap-1.5"
            disabled={!exportData}
            onClick={() => {
              if (exportData) downloadAuthJson(exportData);
            }}
          >
            <Download className="h-4 w-4" />
            Download
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
