import { afterEach, describe, expect, it } from "vitest";

import i18n, { normalizeSupportedLanguage, SUPPORTED_LANGUAGES } from "@/i18n";
import en from "@/i18n/locales/en.json";
import ja from "@/i18n/locales/ja.json";

afterEach(async () => {
  await i18n.changeLanguage("en");
});

describe("normalizeSupportedLanguage", () => {
  it("keeps exact supported locales", () => {
    expect(normalizeSupportedLanguage("en")).toBe("en");
    expect(normalizeSupportedLanguage("zh-CN")).toBe("zh-CN");
    expect(normalizeSupportedLanguage("ko")).toBe("ko");
    expect(normalizeSupportedLanguage("ja")).toBe("ja");
  });

  it("normalizes detected regional locales to supported toggle values", () => {
    expect(normalizeSupportedLanguage("en-US")).toBe("en");
    expect(normalizeSupportedLanguage("zh")).toBe("zh-CN");
    expect(normalizeSupportedLanguage("zh-Hans-CN")).toBe("zh-CN");
    expect(normalizeSupportedLanguage("ZH-cn")).toBe("zh-CN");
    expect(normalizeSupportedLanguage("ko-KR")).toBe("ko");
  });

  it.each(["ja-JP", "JA-jp", "ja_JP", "ja-Jpan-JP"])("normalizes Japanese tag %s", (language) => {
    expect(normalizeSupportedLanguage(language)).toBe("ja");
  });

  it("falls back to English for missing or unsupported locales", () => {
    expect(normalizeSupportedLanguage(undefined)).toBe("en");
    expect(normalizeSupportedLanguage("fr-FR")).toBe("en");
  });

  it("keeps normalized Chinese detections on the supported zh-CN resource", async () => {
    await i18n.changeLanguage(normalizeSupportedLanguage("zh"));

    expect(i18n.resolvedLanguage).toBe("zh-CN");
  });
});

describe("locale resources", () => {
  it.each(SUPPORTED_LANGUAGES)("keeps %s translation coverage in sync with English", (language) => {
    expect(Object.keys(i18n.getResourceBundle(language, "translation")).sort()).toEqual(Object.keys(en).sort());
  });

  it("preserves Japanese interpolation variables and inline markup", () => {
    const placeholders = (value: string) => (value.match(/\{\{[^}]+\}\}|<\/?\d+>/g) ?? []).sort();

    for (const key of Object.keys(en) as (keyof typeof en)[]) {
      expect(ja[key].trim(), key).not.toBe("");
      expect(placeholders(ja[key]), key).toEqual(placeholders(en[key]));
    }
  });

  it("renders Japanese plural counts without English fallback", async () => {
    await i18n.changeLanguage("ja");

    expect(i18n.t("apiKeys.accountSelect.selected", { count: 1 })).toBe("1 アカウントを選択中");
    expect(i18n.t("apiKeys.accountSelect.selected", { count: 3 })).toBe("3 アカウントを選択中");
  });
});
