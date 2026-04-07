import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

export type CopyButtonProps = {
  value: string;
  label?: string;
  iconOnly?: boolean;
};

export function CopyButton({ value, label = "Copy", iconOnly = false }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      toast.success("Copied to clipboard");
      setTimeout(() => setCopied(false), 1200);
    } catch {
      toast.error("Failed to copy");
    }
  };

  return (
    <Button
      type="button"
      variant="outline"
      size={iconOnly ? "icon-sm" : "sm"}
      onClick={handleCopy}
      aria-label={copied ? `${label} Copied` : label}
      title={copied ? "Copied" : label}
    >
      {copied ? <Check className={iconOnly ? "h-4 w-4" : "mr-2 h-4 w-4"} /> : <Copy className={iconOnly ? "h-4 w-4" : "mr-2 h-4 w-4"} />}
      {iconOnly ? null : copied ? "Copied" : label}
    </Button>
  );
}
