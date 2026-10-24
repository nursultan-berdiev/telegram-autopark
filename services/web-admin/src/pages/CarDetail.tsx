import { Link, useParams } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { api } from "../lib/api";
import { ActionButton, Async, Card, fmtDate } from "../components/ui";
import type { Car, CarState, Command, CommandResult, Fine } from "../lib/types";

export default function CarDetail() {
  const { id } = useParams();
  const car = useApi<Car>(`/cars/${id}`);
  const state = useApi<CarState>(`/cars/${id}/state`);
  const commands = useApi<Command[]>(`/cars/${id}/commands`);
  const fines = useApi<Fine[]>(`/cars/${id}/fines`);

  const refreshEngine = () => {
    state.reload();
    commands.reload();
  };

  // Возвращает строку-инфо (показывается у кнопки) или бросает ошибку.
  const sendCommand = async (type: "engine_block" | "engine_unblock") => {
    const res = await api.post<CommandResult>(`/cars/${id}/commands`, {
      type,
      arm_if_unsafe: type === "engine_block",
    });
    if (res.ok) return "Команда отправлена";
    const st = res.command.status;
    if (st === "armed") return "Взведено — сработает на безопасной остановке";
    if (st === "sent" || st === "acked" || st === "unconfirmed") return "Команда уже в работе";
    // Отказ гейта (blocked_by_safety) и т.п. — реальная неудача.
    throw new Error(res.reason ?? "команда не выполнена");
  };

  const cancelArmed = async (cid: number) => {
    // Команда могла уйти из armed (гонка с fire_armed) — тогда ok:false и
    // двигатель фактически заблокирован; молча «успех» показывать нельзя.
    const r = await api.post<CommandResult>(`/cars/${id}/commands/${cid}/cancel`);
    if (!r.ok) throw new Error(r.reason ?? "не удалось отменить — проверьте статус");
  };

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

      <Card title="Где машина и двигатель">
        <Async loading={state.loading} error={state.error} data={state.data}>
          {(s) => (
            <>
              <dl className="kv">
                <dt>На связи</dt><dd>{s.online ? "да" : "нет"}</dd>
                <dt>Зажигание</dt><dd>{s.ignition == null ? "—" : s.ignition ? "вкл" : "выкл"}</dd>
                <dt>Двигатель</dt><dd>{s.engine_blocked ? "заблокирован" : "в норме"}</dd>
                <dt>Координаты</dt>
                <dd>{s.lat != null && s.lon != null ? `${s.lat.toFixed(5)}, ${s.lon.toFixed(5)}` : "—"}</dd>
                <dt>Последняя точка</dt><dd>{fmtDate(s.last_ts)}</dd>
              </dl>
              <div className="actions-row">
                <ActionButton
                  label="⛔ Заблокировать двигатель"
                  confirm="Заблокировать двигатель? На едущей машине блокировка взведётся и сработает на ближайшей безопасной остановке."
                  danger
                  onAction={() => sendCommand("engine_block")}
                  onDone={refreshEngine}
                />
                <ActionButton
                  label="✅ Разблокировать"
                  confirm="Разблокировать двигатель?"
                  onAction={() => sendCommand("engine_unblock")}
                  onDone={refreshEngine}
                />
              </div>
            </>
          )}
        </Async>
      </Card>

      <Card title="Штрафы">
        <Async loading={fines.loading} error={fines.error} data={fines.data} empty="Штрафов нет">
          {(rows) => (
            <table>
              <thead><tr><th>Сумма</th><th>Выписан</th><th>Статус</th><th>Оплачен</th><th></th></tr></thead>
              <tbody>
                {rows.map((f) => (
                  <tr key={f.id}>
                    <td>{f.amount ?? "—"}</td>
                    <td>{fmtDate(f.issued_at)}</td>
                    <td>{f.status}</td>
                    <td>{f.paid_at ? fmtDate(f.paid_at) : "нет"}</td>
                    <td className="row-actions">
                      {!f.paid_at && (
                        <ActionButton label="Оплачен" onAction={() => api.post(`/fines/${f.id}/pay`)} onDone={fines.reload} />
                      )}
                      <ActionButton
                        label="Удалить"
                        confirm="Удалить штраф?"
                        danger
                        onAction={() => api.del(`/fines/${f.id}`)}
                        onDone={fines.reload}
                      />
                    </td>
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
              <thead><tr><th>Тип</th><th>Статус</th><th>Источник</th><th>Когда</th><th></th></tr></thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id}>
                    <td>{c.type}</td>
                    <td>{c.status}</td>
                    <td>{c.source ?? "—"}</td>
                    <td>{fmtDate(c.created_at)}</td>
                    <td className="row-actions">
                      {c.status === "armed" && (
                        <ActionButton
                          label="Отменить ожидание"
                          confirm="Отменить взведённую блокировку?"
                          onAction={() => cancelArmed(c.id)}
                          onDone={refreshEngine}
                        />
                      )}
                    </td>
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
