import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { afterEach, describe, expect, it, vi } from "vitest";
import { THEME_KEY, applyTheme, parsePref, readPref, resolveTheme, savePref } from "./theme";

function stubDom(systemDark: boolean) {
  const root = { dataset: {} as Record<string, string> };
  vi.stubGlobal("document", { documentElement: root });
  vi.stubGlobal("window", { matchMedia: () => ({ matches: systemDark }) });
  return root;
}

function stubStorage(initial: Record<string, string> = {}) {
  const data = { ...initial };
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => data[k] ?? null,
    setItem: (k: string, v: string) => (data[k] = v),
    removeItem: (k: string) => delete data[k],
  });
  return data;
}

afterEach(() => vi.unstubAllGlobals());

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

  it("выбор запоминается, «как в системе» стирает запись", () => {
    const data = stubStorage();
    savePref("dark");
    expect(data[THEME_KEY]).toBe("dark");
    expect(readPref()).toBe("dark");
    savePref("system");
    expect(THEME_KEY in data).toBe(false);
    expect(readPref()).toBe("system");
  });

  it("без доступного хранилища — «как в системе» и без падения", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {
        throw new Error("SecurityError");
      },
    });
    expect(readPref()).toBe("system");
    expect(() => savePref("dark")).not.toThrow();
  });

  it("applyTheme ставит data-theme на <html>", () => {
    const root = stubDom(true);
    expect(applyTheme("system")).toBe("dark");
    expect(root.dataset.theme).toBe("dark");
    expect(applyTheme("light")).toBe("light");
    expect(root.dataset.theme).toBe("light");
  });

  // Тот же расчёт до отрисовки делает inline-скрипт index.html: сверяем его с theme.ts
  // на всех сочетаниях, чтобы правка одного места без другого ломала тест.
  it("inline-скрипт index.html считает тему так же, как theme.ts", () => {
    const html = readFileSync(new URL("../../index.html", import.meta.url), "utf8");
    const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
    expect(script).toBeTruthy();
    for (const stored of [null, "light", "dark", "garbage"]) {
      for (const systemDark of [false, true]) {
        const root = { dataset: {} as Record<string, string> };
        runInNewContext(script!, {
          localStorage: { getItem: (k: string) => (k === THEME_KEY ? stored : null) },
          window: { matchMedia: () => ({ matches: systemDark }) },
          document: { documentElement: root },
        });
        expect(root.dataset.theme, `pref=${stored} systemDark=${systemDark}`).toBe(
          resolveTheme(parsePref(stored), systemDark),
        );
      }
    }
  });
});
