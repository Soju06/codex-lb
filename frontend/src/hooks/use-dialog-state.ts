import { useCallback, useState } from "react";

export type DialogState<T = void> = T extends void
  ? {
      open: boolean;
      onOpenChange: (open: boolean) => void;
      show: () => void;
      hide: () => void;
    }
  : {
      open: boolean;
      data: T | null;
      onOpenChange: (open: boolean) => void;
      show: (data: T) => void;
      hide: () => void;
    };

export function useDialogState<T = void>(): DialogState<T> {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<T | null>(null);

  const show = useCallback((value?: T) => {
    setData((value ?? null) as T | null);
    setOpen(true);
  }, []);

  const hide = useCallback(() => {
    setOpen(false);
    setData(null);
  }, []);

  const onOpenChange = useCallback(
    (value: boolean) => {
      if (!value) {
        hide();
      } else {
        setOpen(true);
      }
    },
    [hide],
  );

  return { open, data, onOpenChange, show, hide } as DialogState<T>;
}
