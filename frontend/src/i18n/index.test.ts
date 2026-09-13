import { describe, expect, it } from "vitest";

import i18n, { normalizeSupportedLanguage } from "@/i18n";
import en from "@/i18n/locales/en.json";
import ko from "@/i18n/locales/ko.json";
import zhCN from "@/i18n/locales/zh-CN.json";

describe("locale key parity", () => {
  it.each([
    ["ko", ko],
    ["zh-CN", zhCN],
  ])("%s defines exactly the keys that en defines", (_locale, resource) => {
    expect(Object.keys(resource).sort()).toEqual(Object.keys(en).sort());
  });
});

describe("normalizeSupportedLanguage", () => {
  it("keeps exact supported locales", () => {
    expect(normalizeSupportedLanguage("en")).toBe("en");
    expect(normalizeSupportedLanguage("zh-CN")).toBe("zh-CN");
  });

  it("normalizes detected regional locales to supported toggle values", () => {
    expect(normalizeSupportedLanguage("en-US")).toBe("en");
    expect(normalizeSupportedLanguage("zh")).toBe("zh-CN");
    expect(normalizeSupportedLanguage("zh-Hans-CN")).toBe("zh-CN");
    expect(normalizeSupportedLanguage("ZH-cn")).toBe("zh-CN");
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
