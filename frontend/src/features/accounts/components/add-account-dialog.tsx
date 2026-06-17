import { Plus, Upload } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

export type AddAccountDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImport: () => void;
  onAddAccount: () => void;
};

export function AddAccountDialog({ open, onOpenChange, onImport, onAddAccount }: AddAccountDialogProps) {
  // Close the chooser first, then defer the action to the next frame. Opening a
  // second modal Dialog in the same tick the chooser closes can leave Radix's
  // `pointer-events: none` stuck on <body>, making the next dialog uninteractive.
  const handleSelect = (action: () => void) => {
    onOpenChange(false);
    requestAnimationFrame(() => action());
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add account</DialogTitle>
          <DialogDescription>Choose how you want to add a ChatGPT account.</DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <button
            type="button"
            onClick={() => handleSelect(onAddAccount)}
            className={cn(
              "flex w-full cursor-pointer items-start gap-3 rounded-lg border p-3 text-left transition-colors hover:bg-muted/50",
              "outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]",
            )}
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border bg-muted/50">
              <Plus className="h-4 w-4 text-muted-foreground" />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-medium">Add account</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                Sign in with OAuth (browser or device code)
              </span>
            </span>
          </button>

          <button
            type="button"
            onClick={() => handleSelect(onImport)}
            className={cn(
              "flex w-full cursor-pointer items-start gap-3 rounded-lg border p-3 text-left transition-colors hover:bg-muted/50",
              "outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]",
            )}
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border bg-muted/50">
              <Upload className="h-4 w-4 text-muted-foreground" />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-medium">Import</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                Import an exported auth.json file
              </span>
            </span>
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
