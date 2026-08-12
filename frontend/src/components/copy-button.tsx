import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState, type MouseEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { copyToClipboard } from "@/utils/clipboard";

export type CopyButtonProps = {
  value: string;
  label?: string;
  iconOnly?: boolean;
};

export function CopyButton({ value, label, iconOnly = false }: CopyButtonProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const mountedRef = useRef(false);
  const resetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const labelText = label ?? t("components.copyButton.copy");

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (resetTimerRef.current !== null) {
        clearTimeout(resetTimerRef.current);
      }
    };
  }, []);

  const handleCopy = async (event: MouseEvent<HTMLButtonElement>) => {
    const trigger = event.currentTarget;
    const dialogContainer = trigger.closest("[role='dialog']");

    try {
      const copiedToClipboard = await copyToClipboard(value, {
        container: dialogContainer instanceof HTMLElement ? dialogContainer : undefined,
      });
      if (!mountedRef.current) {
        return;
      }
      if (copiedToClipboard) {
        setCopied(true);
        toast.success(t("components.copyButton.toasts.copied"));
        if (resetTimerRef.current !== null) {
          clearTimeout(resetTimerRef.current);
        }
        resetTimerRef.current = setTimeout(() => {
          resetTimerRef.current = null;
          setCopied(false);
        }, 1200);
        return;
      }

      toast.error(t("components.copyButton.toasts.failed"));
    } catch {
      if (mountedRef.current) {
        toast.error(t("components.copyButton.toasts.failed"));
      }
    }
  };
  const copiedLabel = t("components.copyButton.copied");

  return (
    <Button
      type="button"
      variant="outline"
      size={iconOnly ? "icon-sm" : "sm"}
      onMouseDown={(event) => event.preventDefault()}
      onClick={(event) => void handleCopy(event)}
      aria-label={copied ? t("components.copyButton.copiedAria", { label: labelText }) : labelText}
      title={copied ? copiedLabel : labelText}
    >
      {copied ? <Check className={iconOnly ? "h-4 w-4" : "mr-2 h-4 w-4"} /> : <Copy className={iconOnly ? "h-4 w-4" : "mr-2 h-4 w-4"} />}
      {iconOnly ? null : copied ? copiedLabel : labelText}
    </Button>
  );
}
