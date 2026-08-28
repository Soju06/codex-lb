import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import ko from "./locales/ko.json";
import zhCN from "./locales/zh-CN.json";

export const SUPPORTED_LANGUAGES = ["en", "zh-CN", "ko"] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

export const LANGUAGE_STORAGE_KEY = "codex-lb-language";

const resources = {
  en: { translation: en },
  "zh-CN": { translation: zhCN },
  ko: { translation: ko },
} as const;

export function normalizeSupportedLanguage(lng: string | null | undefined): SupportedLanguage {
  if (!lng) {
    return "en";
  }
  const exactMatch = SUPPORTED_LANGUAGES.find((supported) => supported.toLowerCase() === lng.toLowerCase());
  if (exactMatch) {
    return exactMatch;
  }
  const baseLanguage = lng.split(/[-_]/, 1)[0]?.toLowerCase();
  if (baseLanguage === "zh") {
    return "zh-CN";
  }
  if (baseLanguage === "en") {
    return "en";
  }
  if (baseLanguage === "ko") {
    return "ko";
  }
  return "en";
}

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    supportedLngs: [...SUPPORTED_LANGUAGES],
    fallbackLng: "en",
    load: "currentOnly",
    interpolation: { escapeValue: false },
    detection: {
      order: ["querystring", "localStorage", "navigator"],
      lookupQuerystring: "lang",
      lookupLocalStorage: LANGUAGE_STORAGE_KEY,
      caches: ["localStorage"],
      convertDetectedLanguage: normalizeSupportedLanguage,
    },
    returnNull: false,
  });

function applyHtmlLang(lng: string): void {
  if (typeof document === "undefined") {
    return;
  }
  document.documentElement.lang = lng;
}

applyHtmlLang(normalizeSupportedLanguage(i18n.resolvedLanguage ?? i18n.language));
i18n.on("languageChanged", (lng) => applyHtmlLang(normalizeSupportedLanguage(lng)));

export default i18n;
