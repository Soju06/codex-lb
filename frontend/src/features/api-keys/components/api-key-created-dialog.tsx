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

export type ApiKeyCreatedDialogProps = {
  open: boolean;
  apiKey: string | null;
  onOpenChange: (open: boolean) => void;
};

export function ApiKeyCreatedDialog({ open, apiKey, onOpenChange }: ApiKeyCreatedDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>API key created</DialogTitle>
          <DialogDescription>
            Copy this key now. It will not be shown again after closing this dialog.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2 rounded-lg border bg-muted/30 p-3">
          <p className="break-all font-mono text-xs">{apiKey || "-"}</p>
          {apiKey ? <CopyButton value={apiKey} /> : null}
        </div>

        <DialogFooter>
          <Button type="button" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
