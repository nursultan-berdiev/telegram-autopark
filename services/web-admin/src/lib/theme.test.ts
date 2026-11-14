import { describe, expect, it } from "vitest";
import { parsePref, resolveTheme } from "./theme";

describe("тема оформления", () => {
  it("неизвестное или пустое значение — «как в системе»", () => {
    expect(parsePref(null)).toBe("system");
    expect(parsePref("")).toBe("system");
    expect(parsePref("blue")).toBe("system");
    expect(parsePref("dark")).toBe("dark");
    expect(parsePref("light")).toBe("light");
  });

  it("«как в системе» следует настройке ОС, явный выбор — нет", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });
});
