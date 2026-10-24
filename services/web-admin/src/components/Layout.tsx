import { NavLink, Outlet } from "react-router-dom";
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
          {/* Карта Traccar — SSO появится в Phase 4 */}
          <span className="nav-disabled" title="Появится в следующем этапе">
            Карта (скоро)
          </span>
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
