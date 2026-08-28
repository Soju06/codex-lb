import { Plus, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";

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
  const { t } = useTranslation();
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
          <DialogTitle>{t("accounts.addDialog.title")}</DialogTitle>
          <DialogDescription>{t("accounts.addDialog.description")}</DialogDescription>
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
              <span className="block text-sm font-medium">{t("accounts.addDialog.oauthTitle")}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {t("accounts.addDialog.oauthDescription")}
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
              <span className="block text-sm font-medium">{t("common.actions.import")}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {t("accounts.addDialog.importDescription")}
              </span>
            </span>
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
