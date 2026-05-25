import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import zhCN from "./locales/zh-CN.json";

export const SUPPORTED_LANGUAGES = ["en", "zh-CN"] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

export const LANGUAGE_STORAGE_KEY = "codex-lb-language";

function normalizeDetectedLanguage(lng: string): string {
  return lng.toLowerCase().startsWith("zh") ? "zh-CN" : lng;
}

const resources = {
  en: { translation: en },
  "zh-CN": { translation: zhCN },
} as const;

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
      convertDetectedLanguage: normalizeDetectedLanguage,
    },
    returnNull: false,
  });

function applyHtmlLang(lng: string): void {
  if (typeof document === "undefined") {
    return;
  }
  document.documentElement.lang = lng;
}

applyHtmlLang(i18n.resolvedLanguage ?? i18n.language ?? "en");
i18n.on("languageChanged", applyHtmlLang);

export default i18n;
