import { useSearchParams } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { Async, fmtDate } from "../components/ui";
import type { AuditRow, OutboundRow, TaskRun, Alert } from "../lib/types";

type Tab = "audit" | "outbound" | "task-runs" | "alerts";
const TABS: { key: Tab; label: string }[] = [
  { key: "audit", label: "Аудит действий" },
  { key: "task-runs", label: "Прогоны задач" },
  { key: "outbound", label: "Очередь отправок" },
  { key: "alerts", label: "Алерты" },
];

function outState(m: OutboundRow): string {
  if (m.sent_at) return "отправлено";
  if (m.failed_at) return "сдались";
  if (m.cancelled_at) return "отменено";
  return "в очереди";
}

export default function Journals() {
  // Вкладка — производная от URL (?tab=), не локальный state: иначе переход на
  // уже открытый /journals (колокольчик → ?tab=alerts) не переключал бы её,
  // т.к. компонент не перемонтируется. Переключение пишет query через setParams.
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab");
  const tab: Tab = TABS.some((t) => t.key === requested) ? (requested as Tab) : "audit";
  const setTab = (t: Tab) => setParams({ tab: t }, { replace: true });
  const audit = useApi<AuditRow[]>(tab === "audit" ? "/audit?limit=200" : null);
  const runs = useApi<TaskRun[]>(tab === "task-runs" ? "/task-runs?limit=100" : null);
  const out = useApi<OutboundRow[]>(tab === "outbound" ? "/outbound-messages?limit=200" : null);
  const alerts = useApi<Alert[]>(tab === "alerts" ? "/alerts?status=open" : null);

  return (
    <>
      <h1>Журналы</h1>
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.key} className={tab === t.key ? "active" : ""} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "audit" && (
        <Async loading={audit.loading} error={audit.error} data={audit.data} empty="Пусто">
          {(rows) => (
            <table>
              <thead><tr><th>Когда</th><th>Кто</th><th>Откуда</th><th>Действие</th><th>Код</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td>{fmtDate(r.created_at)}</td><td>{r.actor_tg_user_id ?? "—"}</td>
                    <td>{r.source}</td><td>{r.method} {r.path}</td><td>{r.status_code}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Async>
      )}

      {tab === "task-runs" && (
        <Async loading={runs.loading} error={runs.error} data={runs.data} empty="Пусто">
          {(rows) => (
            <table>
              <thead><tr><th>Начало</th><th>Задача</th><th>Статус</th><th>Детали</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className={r.status === "failed" ? "overdue" : ""}>
                    <td>{fmtDate(r.started_at)}</td><td>{r.task.split(".").pop()}</td>
                    <td>{r.status}</td><td>{r.detail ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Async>
      )}

      {tab === "outbound" && (
        <Async loading={out.loading} error={out.error} data={out.data} empty="Пусто">
          {(rows) => (
            <table>
              <thead><tr><th>Когда</th><th>Кому</th><th>Тип</th><th>Статус</th></tr></thead>
              <tbody>
                {rows.map((m) => (
                  <tr key={m.id}>
                    <td>{fmtDate(m.created_at)}</td><td>{m.recipient_tg_user_id}</td>
                    <td>{m.kind}</td><td>{outState(m)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Async>
      )}

      {tab === "alerts" && (
        <Async loading={alerts.loading} error={alerts.error} data={alerts.data} empty="Пусто">
          {(rows) => (
            <table>
              <thead><tr><th>Когда</th><th>Машина</th><th>Тип</th><th>Важность</th></tr></thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.id}>
                    <td>{fmtDate(a.triggered_at)}</td><td>#{a.car_id}</td>
                    <td>{a.type}</td><td>{a.severity}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Async>
      )}
    </>
  );
}
