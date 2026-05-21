import { afterEach, describe, expect, it } from "vitest";

import i18n, { LANGUAGE_STORAGE_KEY } from "@/i18n";

describe("i18n", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("preserves the supported zh-CN locale tag", async () => {
    await i18n.changeLanguage("zh-CN");

    expect(i18n.language).toBe("zh-CN");
    expect(i18n.resolvedLanguage).toBe("zh-CN");
    expect(document.documentElement.lang).toBe("zh-CN");
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("zh-CN");
  });
});
