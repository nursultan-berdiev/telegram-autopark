import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useApi } from "../lib/hooks";
import type { Alert } from "../lib/types";

const POLL_MS = 60000; // как часто обновлять счётчик алертов в фоне

// Колокольчик = индикатор открытых алертов: бейдж с числом, клик открывает
// вкладку «Алерты» в журналах. Счётчик обновляется при навигации, по возврату
// фокуса в окно и раз в минуту, иначе бейдж «застыл» бы на значении при входе.
export default function Bell() {
  const nav = useNavigate();
  const loc = useLocation();
  const alerts = useApi<Alert[]>("/alerts?status=open");
  const reload = alerts.reload;
  const firstNav = useRef(true);

  // Навигация: перечитать (кроме первого рендера — useApi уже загрузил на монтировании).
  useEffect(() => {
    if (firstNav.current) {
      firstNav.current = false;
      return;
    }
    reload();
  }, [loc.pathname, reload]);

  // Фокус окна + поллинг.
  useEffect(() => {
    const onFocus = () => reload();
    window.addEventListener("focus", onFocus);
    const id = window.setInterval(reload, POLL_MS);
    return () => {
      window.removeEventListener("focus", onFocus);
      window.clearInterval(id);
    };
  }, [reload]);

  const failed = Boolean(alerts.error);
  const n = alerts.data?.length ?? 0;
  const title = failed
    ? "Не удалось загрузить алерты"
    : n
      ? `Открытых алертов: ${n}`
      : "Открытых алертов нет";
  const aria = failed ? "Уведомления: ошибка загрузки" : n ? `Уведомления: открытых алертов ${n}` : "Уведомлений нет";

  return (
    <button className="iconbtn" aria-label={aria} title={title} onClick={() => nav("/journals?tab=alerts")}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6z" />
        <path d="M10 20a2 2 0 0 0 4 0" />
      </svg>
      {failed ? <span className="dot err">!</span> : n > 0 && <span className="dot">{n > 9 ? "9+" : n}</span>}
    </button>
  );
}
