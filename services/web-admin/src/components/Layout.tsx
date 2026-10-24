import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

// Иконки — те же глифы, что в утверждённом макете (вариант 5).
const NAV = [
  { to: "/", label: "Дашборд", end: true, icon: "◱" },
  { to: "/cars", label: "Автопарк", icon: "▦" },
  { to: "/drivers", label: "Водители", icon: "◍" },
  { to: "/announcements", label: "Объявления", icon: "◎" },
  { to: "/periodic", label: "Задачи", icon: "◴" },
  { to: "/reports", label: "Отчёты", icon: "▤" },
  { to: "/journals", label: "Журналы", icon: "≡" },
];

// Карта Traccar открывается в новой вкладке по короткоживущему токену автологина:
// отдельный сервис на своём домене, встраивать в iframe не нужно.
function MapLink({ onNavigate }: { onNavigate?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);
  const open = async () => {
    onNavigate?.();
    setBusy(true);
    setErr(false);
    // Вкладку открываем СИНХРОННО в обработчике клика: Safari (и другие при
    // медленном ответе) блокируют popup, открытый уже после await.
    const tab = window.open("", "_blank");
    if (tab) tab.opener = null; // noopener вручную — имя окна не задаём
    try {
      const { url } = await api.post<{ url: string }>("/traccar/sso");
      if (tab) tab.location.href = url;
      else setErr(true); // popup заблокирован браузером
    } catch {
      setErr(true);
      if (tab) tab.close();
    } finally {
      setBusy(false);
    }
  };
  return (
    <button className="nav-map map" onClick={open} disabled={busy} title="Открыть карту Traccar">
      <span className="i">◉</span>
      {busy ? "Карта…" : err ? "Карта — ошибка" : "Карта ↗"}
    </button>
  );
}

export default function Layout() {
  const { me, logout } = useAuth();
  const [navOpen, setNavOpen] = useState(false);
  const closeNav = () => setNavOpen(false);

  return (
    <div className={`shell${navOpen ? " nav-open" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <span className="m">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 11l1.5-4.5A2 2 0 0 1 8.4 5h7.2a2 2 0 0 1 1.9 1.5L19 11v5H5z" />
              <circle cx="7.5" cy="16" r="1.3" />
              <circle cx="16.5" cy="16" r="1.3" />
            </svg>
          </span>
          Автопарк
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              onClick={closeNav}
              className={({ isActive }) => (isActive ? "on" : "")}
            >
              <span className="i">{n.icon}</span>
              {n.label}
            </NavLink>
          ))}
          <div className="sep" />
          <MapLink onNavigate={closeNav} />
        </nav>
        <div className="side-foot">
          <div className="av">А</div>
          <div className="who-wrap">
            <div className="who">Администратор</div>
            <div className="role">tg {me?.tg_user_id}</div>
          </div>
          <button className="link foot-logout" onClick={logout}>
            Выйти
          </button>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <button className="burger" aria-label="Открыть меню" onClick={() => setNavOpen(true)}>
            ☰
          </button>
          <label className="search">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="11" cy="11" r="7" />
              <path d="M21 21l-4.3-4.3" />
            </svg>
            <input placeholder="Поиск по номеру, водителю, штрафу…" />
          </label>
          <span className="sp" />
          <button className="iconbtn" aria-label="Уведомления">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6z" />
              <path d="M10 20a2 2 0 0 0 4 0" />
            </svg>
            <span className="dot" />
          </button>
          <div className="av" title={`tg ${me?.tg_user_id}`}>
            А
          </div>
        </header>
        <div className="page">
          <Outlet />
        </div>
      </main>

      <div className="scrim" onClick={closeNav} />
    </div>
  );
}
