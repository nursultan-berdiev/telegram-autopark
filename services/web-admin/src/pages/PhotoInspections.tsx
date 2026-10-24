import { Link } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { Async, fmtDate } from "../components/ui";
import type { PhotoStatus } from "../lib/types";

// Локальная дата парка приходит как YYYY-MM-DD — показываем как есть (без TZ-сдвига).
function fmtDay(d: string | null): string {
  if (!d) return "—";
  const [y, m, day] = d.split("-");
  return y && m && day ? `${day}.${m}.${y}` : d;
}

export default function PhotoInspections() {
  const status = useApi<PhotoStatus[]>("/photo-inspections/status");
  return (
    <>
      <h1>Фотоконтроль</h1>
      <p className="muted hint" style={{ marginTop: 0 }}>
        Осмотр машины водитель сдаёт через бота. Здесь — статус по каждому и альбом осмотра.
      </p>
      <Async loading={status.loading} error={status.error} data={status.data} empty="Нет водителей с машиной">
        {(rows) => (
          <table>
            <thead>
              <tr>
                <th>Водитель</th>
                <th>Машина</th>
                <th>След. срок</th>
                <th>Просрочка</th>
                <th>Последний осмотр</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.driver_id} className={s.overdue_days > 0 ? "overdue" : ""}>
                  <td>
                    <Link to={`/drivers/${s.driver_id}`}>{s.driver_name}</Link>
                  </td>
                  <td>
                    <Link to={`/cars/${s.car_id}`}>{s.car_plate}</Link>
                  </td>
                  <td>{fmtDay(s.next_due_on)}</td>
                  <td>{s.overdue_days > 0 ? `${s.overdue_days} дн` : "—"}</td>
                  <td>
                    {s.last_inspection_id ? (
                      <Link to={`/photo-inspections/${s.last_inspection_id}`}>
                        {fmtDate(s.last_inspection_at)}
                      </Link>
                    ) : (
                      "осмотров нет"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Async>
    </>
  );
}
