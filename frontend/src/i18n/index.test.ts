import { describe, expect, it } from "vitest";

import i18n, { normalizeSupportedLanguage } from "@/i18n";

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
