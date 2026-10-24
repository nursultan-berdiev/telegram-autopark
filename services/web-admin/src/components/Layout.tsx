import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

const NAV = [
  { to: "/", label: "Дашборд", end: true },
  { to: "/cars", label: "Автопарк" },
  { to: "/drivers", label: "Водители" },
  { to: "/announcements", label: "Объявления" },
  { to: "/periodic", label: "Задачи" },
  { to: "/reports", label: "Отчёты" },
  { to: "/journals", label: "Журналы" },
];

// Карта Traccar открывается в новой вкладке по короткоживущему токену автологина:
// отдельный сервис на своём домене, встраивать в iframe не нужно.
function MapLink() {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);
  const open = async () => {
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
    <button className="nav-map" onClick={open} disabled={busy} title="Открыть карту Traccar">
      {busy ? "Карта…" : err ? "Карта — ошибка" : "Карта ↗"}
    </button>
  );
}

export default function Layout() {
  const { me, logout } = useAuth();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">🚗 Автопарк</div>
        <nav>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {n.label}
            </NavLink>
          ))}
          <MapLink />
        </nav>
      </aside>
      <main className="content">
        <header className="topbar">
          <span className="muted">admin · tg {me?.tg_user_id}</span>
          <button className="link" onClick={logout}>
            Выйти
          </button>
        </header>
        <div className="page">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
