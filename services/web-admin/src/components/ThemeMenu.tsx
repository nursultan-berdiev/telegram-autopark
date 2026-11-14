import { useEffect, useRef, useState } from "react";
import {
  applyTheme,
  onSystemThemeChange,
  readPref,
  resolveTheme,
  savePref,
  systemPrefersDark,
  type Theme,
  type ThemePref,
} from "../lib/theme";

const OPTIONS: { key: ThemePref; label: string }[] = [
  { key: "light", label: "Светлая" },
  { key: "dark", label: "Тёмная" },
  { key: "system", label: "Как в системе" },
];

export default function ThemeMenu() {
  const [pref, setPref] = useState<ThemePref>(readPref);
  // В DOM пишет только эффект: до него атрибут уже выставил inline-скрипт index.html.
  const [theme, setTheme] = useState<Theme>(() => resolveTheme(pref, systemPrefersDark()));
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setTheme(applyTheme(pref));
    if (pref !== "system") return;
    return onSystemThemeChange(() => setTheme(applyTheme("system")));
  }, [pref]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const choose = (next: ThemePref) => {
    savePref(next);
    setPref(next);
    setOpen(false);
  };

  return (
    <div className="theme-menu" ref={box}>
      <button
        type="button"
        className="iconbtn"
        aria-label="Тема оформления"
        aria-expanded={open}
        title={theme === "dark" ? "Тема: тёмная" : "Тема: светлая"}
        onClick={() => setOpen((o) => !o)}
      >
        {theme === "dark" ? (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />
          </svg>
        ) : (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </svg>
        )}
      </button>
      {open && (
        <div className="menu" role="group" aria-label="Тема оформления">
          {OPTIONS.map((o) => (
            <button
              key={o.key}
              type="button"
              aria-pressed={pref === o.key}
              className={pref === o.key ? "on" : ""}
              onClick={() => choose(o.key)}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
