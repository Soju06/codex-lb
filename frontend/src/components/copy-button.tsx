import { Check, Copy } from "lucide-react";
import { useState, type MouseEvent } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { copyToClipboard } from "@/utils/clipboard";

export type CopyButtonProps = {
  value: string;
  label?: string;
  iconOnly?: boolean;
  className?: string;
};

export function CopyButton({
  value,
  label = "Copy",
  iconOnly = false,
  className,
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async (event: MouseEvent<HTMLButtonElement>) => {
    const trigger = event.currentTarget;
    const dialogContainer = trigger.closest("[role='dialog']");

    try {
      const copiedToClipboard = await copyToClipboard(value, {
        container: dialogContainer instanceof HTMLElement ? dialogContainer : undefined,
      });
      if (copiedToClipboard) {
        setCopied(true);
        toast.success("Copied to clipboard");
        setTimeout(() => setCopied(false), 1200);
        return;
      }

      toast.error("Failed to copy");
    } catch {
      toast.error("Failed to copy");
    }
  };

  return (
    <Button
      type="button"
      variant="outline"
      size={iconOnly ? "icon-sm" : "sm"}
      className={cn(className)}
      onMouseDown={(event) => event.preventDefault()}
      onClick={(event) => void handleCopy(event)}
      aria-label={copied ? `${label} Copied` : label}
      title={copied ? "Copied" : label}
    >
      {copied ? <Check className={iconOnly ? "h-4 w-4" : "mr-2 h-4 w-4"} /> : <Copy className={iconOnly ? "h-4 w-4" : "mr-2 h-4 w-4"} />}
      {iconOnly ? null : copied ? "Copied" : label}
    </Button>
  );
}
