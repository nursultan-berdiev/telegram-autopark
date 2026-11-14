// Тема оформления: выбор хранится в браузере, «как в системе» — по умолчанию.
// Тот же расчёт до отрисовки делает inline-скрипт в index.html (иначе тёмная
// тема мигала бы светлой) — меняя ключ или правила, правь оба места.
export type ThemePref = "light" | "dark" | "system";
export type Theme = "light" | "dark";

export const THEME_KEY = "autopark-theme";

export function parsePref(raw: string | null): ThemePref {
  return raw === "light" || raw === "dark" ? raw : "system";
}

export function resolveTheme(pref: ThemePref, systemDark: boolean): Theme {
  if (pref === "system") return systemDark ? "dark" : "light";
  return pref;
}

const systemQuery = () => window.matchMedia("(prefers-color-scheme: dark)");

export function readPref(): ThemePref {
  try {
    return parsePref(localStorage.getItem(THEME_KEY));
  } catch {
    return "system"; // хранилище недоступно (приватный режим) — живём без запоминания
  }
}

export function savePref(pref: ThemePref): void {
  try {
    if (pref === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, pref);
  } catch {
    // см. readPref
  }
}

export function applyTheme(pref: ThemePref): Theme {
  const theme = resolveTheme(pref, systemQuery().matches);
  document.documentElement.dataset.theme = theme;
  return theme;
}

export function onSystemThemeChange(cb: () => void): () => void {
  const query = systemQuery();
  query.addEventListener("change", cb);
  return () => query.removeEventListener("change", cb);
}
