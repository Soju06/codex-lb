import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from "@/i18n";

const LANGUAGE_LABEL_KEY: Record<SupportedLanguage, string> = {
  en: "common.english",
  "zh-CN": "common.chinese",
};

export function LanguageToggle() {
  const { t, i18n } = useTranslation();
  const current = (i18n.resolvedLanguage ?? i18n.language ?? "en") as SupportedLanguage;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          aria-label={t("common.language")}
          className="press-scale hidden h-8 w-8 rounded-lg text-muted-foreground hover:text-foreground sm:inline-flex"
        >
          <Languages className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[140px]">
        {SUPPORTED_LANGUAGES.map((lng) => (
          <DropdownMenuItem
            key={lng}
            onSelect={() => {
              if (lng !== current) {
                void i18n.changeLanguage(lng);
              }
            }}
            className={lng === current ? "font-semibold" : undefined}
          >
            {t(LANGUAGE_LABEL_KEY[lng])}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function LanguageToggleMobile() {
  const { t, i18n } = useTranslation();
  const current = (i18n.resolvedLanguage ?? i18n.language ?? "en") as SupportedLanguage;

  return (
    <div className="flex flex-col gap-0.5">
      {SUPPORTED_LANGUAGES.map((lng) => (
        <button
          key={lng}
          type="button"
          onClick={() => {
            if (lng !== current) {
              void i18n.changeLanguage(lng);
            }
          }}
          className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors hover:bg-muted ${
            lng === current
              ? "font-semibold text-foreground"
              : "font-medium text-muted-foreground hover:text-foreground"
          }`}
        >
          <Languages className="h-3.5 w-3.5" aria-hidden="true" />
          {t(LANGUAGE_LABEL_KEY[lng])}
        </button>
      ))}
    </div>
  );
}
