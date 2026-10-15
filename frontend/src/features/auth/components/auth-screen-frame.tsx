import type { PropsWithChildren } from "react";
import { useTranslation } from "react-i18next";

import { CodexLogo } from "@/components/brand/codex-logo";
import { cn } from "@/lib/utils";

export type AuthScreenFrameProps = PropsWithChildren<{
  title?: string;
  subtitle?: string;
  className?: string;
}>;

/** The centred, decorated page used by every pre-app screen (login, invite, enrollment). */
export function AuthScreenFrame({ title, subtitle, className, children }: AuthScreenFrameProps) {
  const { t } = useTranslation();
  return (
    <div className="relative flex min-h-screen items-center justify-center p-4">
      {/* Background decoration */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-1/4 -right-1/4 h-[600px] w-[600px] rounded-full bg-primary/5 blur-3xl" />
        <div className="absolute -bottom-1/4 -left-1/4 h-[500px] w-[500px] rounded-full bg-primary/3 blur-3xl" />
        <div className="absolute bottom-0 left-1/2 h-[400px] w-[400px] -translate-x-1/2 rounded-full bg-primary/4 blur-3xl" />
      </div>

      <div className={cn("relative w-full max-w-sm animate-fade-in-up", className)}>
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 shadow-sm ring-2 ring-primary/10 ring-offset-2 ring-offset-background">
            <CodexLogo size={28} className="text-primary" />
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{title ?? t("auth.appTitle")}</h1>
            <p className="mt-0.5 text-sm text-muted-foreground">{subtitle ?? t("auth.appSubtitle")}</p>
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}
