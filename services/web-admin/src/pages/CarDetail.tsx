import { Link, useParams } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { Async, Card, fmtDate } from "../components/ui";
import type { Car, CarState, Command, Fine } from "../lib/types";

export default function CarDetail() {
  const { id } = useParams();
  const car = useApi<Car>(`/cars/${id}`);
  const state = useApi<CarState>(`/cars/${id}/state`);
  const commands = useApi<Command[]>(`/cars/${id}/commands`);
  const fines = useApi<Fine[]>(`/cars/${id}/fines`);

  return (
    <>
      <p>
        <Link to="/cars">← К списку</Link>
      </p>
      <h1>Машина {car.data?.plate ?? id}</h1>

      <Card title="Карточка">
        <Async loading={car.loading} error={car.error} data={car.data}>
          {(c) => (
            <dl className="kv">
              <dt>Номер</dt><dd>{c.plate}</dd>
              <dt>Модель</dt><dd>{c.model ?? "—"}</dd>
              <dt>Статус</dt><dd>{c.status === "free" ? "свободна" : "занята"}</dd>
              <dt>Водитель</dt>
              <dd>{c.driver_id ? <Link to={`/drivers/${c.driver_id}`}>{c.driver_name}</Link> : "—"}</dd>
            </dl>
          )}
        </Async>
      </Card>

      <Card title="Где машина">
        <Async loading={state.loading} error={state.error} data={state.data}>
          {(s) => (
            <dl className="kv">
              <dt>На связи</dt><dd>{s.online ? "да" : "нет"}</dd>
              <dt>Зажигание</dt><dd>{s.ignition == null ? "—" : s.ignition ? "вкл" : "выкл"}</dd>
              <dt>Двигатель</dt><dd>{s.engine_blocked ? "заблокирован" : "в норме"}</dd>
              <dt>Координаты</dt>
              <dd>{s.lat != null && s.lon != null ? `${s.lat.toFixed(5)}, ${s.lon.toFixed(5)}` : "—"}</dd>
              <dt>Последняя точка</dt><dd>{fmtDate(s.last_ts)}</dd>
            </dl>
          )}
        </Async>
      </Card>

      <Card title="Штрафы">
        <Async loading={fines.loading} error={fines.error} data={fines.data} empty="Штрафов нет">
          {(rows) => (
            <table>
              <thead><tr><th>Сумма</th><th>Выписан</th><th>Статус</th><th>Оплачен</th></tr></thead>
              <tbody>
                {rows.map((f) => (
                  <tr key={f.id}>
                    <td>{f.amount ?? "—"}</td>
                    <td>{fmtDate(f.issued_at)}</td>
                    <td>{f.status}</td>
                    <td>{f.paid_at ? fmtDate(f.paid_at) : "нет"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Async>
      </Card>

      <Card title="Команды двигателя (аудит)">
        <Async loading={commands.loading} error={commands.error} data={commands.data} empty="Команд не было">
          {(rows) => (
            <table>
              <thead><tr><th>Тип</th><th>Статус</th><th>Источник</th><th>Когда</th></tr></thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id}>
                    <td>{c.type}</td>
                    <td>{c.status}</td>
                    <td>{c.source ?? "—"}</td>
                    <td>{fmtDate(c.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Async>
      </Card>
    </>
  );
}
