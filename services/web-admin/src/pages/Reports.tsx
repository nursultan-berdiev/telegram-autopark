import { useState } from "react";
import { useApi } from "../lib/hooks";
import { Async, fmtDate } from "../components/ui";
import type { CarDriverRow, UpcomingRow } from "../lib/types";

type Tab = "cars-drivers" | "upcoming" | "by-driver" | "by-car";
const TABS: { key: Tab; label: string }[] = [
  { key: "cars-drivers", label: "Машины и водители" },
  { key: "upcoming", label: "Кому скоро платить" },
  { key: "by-driver", label: "Выписка по водителям" },
  { key: "by-car", label: "Выписка по машинам" },
];

function GenericTable({ rows }: { rows: Record<string, unknown>[] }) {
  const cols = Object.keys(rows[0]);
  return (
    <table>
      <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>{cols.map((c) => <td key={c}>{String(r[c] ?? "—")}</td>)}</tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Reports() {
  const [tab, setTab] = useState<Tab>("cars-drivers");
  const cd = useApi<CarDriverRow[]>(tab === "cars-drivers" ? "/reports/cars-drivers" : null);
  const up = useApi<UpcomingRow[]>(tab === "upcoming" ? "/reports/upcoming" : null);
  const bd = useApi<Record<string, unknown>[]>(tab === "by-driver" ? "/reports/by-driver" : null);
  const bc = useApi<Record<string, unknown>[]>(tab === "by-car" ? "/reports/by-car" : null);

  return (
    <>
      <h1>Отчёты</h1>
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.key} className={tab === t.key ? "active" : ""} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "cars-drivers" && (
        <Async loading={cd.loading} error={cd.error} data={cd.data} empty="Пусто">
          {(rows) => (
            <table>
              <thead><tr><th>Номер</th><th>Модель</th><th>Статус</th><th>Водитель</th><th>Телефон</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.plate}>
                    <td>{r.plate}</td><td>{r.model ?? "—"}</td>
                    <td>{r.status === "free" ? "свободна" : "занята"}</td>
                    <td>{r.driver_name ?? "—"}</td>
                    <td>{r.driver_phone ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Async>
      )}

      {tab === "upcoming" && (
        <Async loading={up.loading} error={up.error} data={up.data} empty="Пусто">
          {(rows) => (
            <table>
              <thead><tr><th>Водитель</th><th>Машина</th><th>Срок</th><th>Статус</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.driver_id} className={r.is_overdue ? "overdue" : ""}>
                    <td>{r.driver_name}</td><td>{r.plate ?? "—"}</td>
                    <td>{fmtDate(r.next_due_date)}</td><td>{r.summary}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Async>
      )}

      {tab === "by-driver" && (
        <Async loading={bd.loading} error={bd.error} data={bd.data} empty="Пусто">
          {(rows) => <GenericTable rows={rows} />}
        </Async>
      )}

      {tab === "by-car" && (
        <Async loading={bc.loading} error={bc.error} data={bc.data} empty="Пусто">
          {(rows) => <GenericTable rows={rows} />}
        </Async>
      )}
    </>
  );
}
