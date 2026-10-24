import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { api } from "../lib/api";
import { ActionButton, Async, Card, ErrorBox, Loading, fmtDate } from "../components/ui";
import { MediaImage } from "../components/Media";
import DocumentList from "../components/DocumentList";
import type { Car, CarState, Command, CommandResult, Fine, Maintenance, Tracker } from "../lib/types";

function TrackerCard({ carId }: { carId: string }) {
  // Трекер может быть не привязан (ответ null) — это НЕ «загрузка»; Async бы их
  // не различил, поэтому состояние рисуем вручную по loading/error.
  const { data, error, loading, reload } = useApi<Tracker | null>(`/cars/${carId}/tracker`);
  const [provider, setProvider] = useState("traccar");
  const [externalId, setExternalId] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!externalId.trim()) {
      setErr("Укажите ID устройства");
      setOk(false);
      return;
    }
    setBusy(true);
    setErr(null);
    setOk(false);
    try {
      await api.put(`/cars/${carId}/tracker`, {
        provider: provider.trim() || "traccar",
        external_id: externalId.trim(),
        // Сохранённый config трекера не трогаем: бэкенд при PUT переписывает
        // его целиком, а форма его не показывает — иначе перепривязка обнулила бы.
        config: data?.config ?? null,
      });
      setOk(true);
      setExternalId("");
      reload();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Трекер">
      {loading ? (
        <Loading />
      ) : error ? (
        <ErrorBox error={error} />
      ) : data ? (
        <>
          <dl className="kv">
            <dt>Провайдер</dt><dd>{data.provider}</dd>
            <dt>Устройство</dt><dd>{data.external_id}</dd>
          </dl>
          <div className="actions-row">
            <ActionButton
              label="Отвязать"
              confirm="Отвязать трекер от машины? База пробега ТО станет недостоверной до следующей отметки «выполнено»."
              danger
              onAction={() => api.del(`/cars/${carId}/tracker`)}
              onDone={reload}
            />
          </div>
        </>
      ) : (
        <p className="muted">Трекер не привязан.</p>
      )}
      <form className="form-grid" onSubmit={submit}>
        <label>Провайдер<input value={provider} onChange={(e) => setProvider(e.target.value)} /></label>
        <label>
          ID устройства
          <input value={externalId} onChange={(e) => setExternalId(e.target.value)} placeholder="IMEI / Traccar id" />
        </label>
        <div className="form-actions">
          <button disabled={busy}>{busy ? "…" : data ? "Перепривязать" : "Привязать"}</button>
          {err && <span className="error">{err}</span>}
          {ok && <span className="info">Сохранено</span>}
        </div>
      </form>
    </Card>
  );
}

function MaintenanceCard({ carId }: { carId: string }) {
  const { data, error, loading, reload } = useApi<Maintenance[]>(`/cars/${carId}/maintenance`);
  const [type, setType] = useState("oil");
  const [intervalKm, setIntervalKm] = useState("10000");
  const [lastKm, setLastKm] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  // Предзаполнить форму из строки: правка существующего типа иначе ушла бы с
  // пустым «Последним ТО» (null), а бэкенд на null ставит текущий одометр —
  // база ТО сбросилась бы на «сейчас», и просрочка пропала бы без «Выполнено».
  const edit = (m: Maintenance) => {
    setType(m.type);
    setIntervalKm(String(Math.trunc(Number(m.interval_km))));
    setLastKm(String(Math.trunc(Number(m.last_service_km))));
    setNote(m.note ?? "");
    setErr(null);
    setOk(false);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!type.trim()) {
      setErr("Укажите тип ТО");
      setOk(false);
      return;
    }
    // Целое положительное: Number(">0") пропустил бы "1e3"/"1.5"/"0x10".
    if (!/^\d+$/.test(intervalKm) || Number(intervalKm) <= 0) {
      setErr("Интервал — целое число км больше нуля");
      setOk(false);
      return;
    }
    if (lastKm.trim() && !/^\d+$/.test(lastKm.trim())) {
      setErr("Последнее ТО — целое число км");
      setOk(false);
      return;
    }
    setBusy(true);
    setErr(null);
    setOk(false);
    try {
      await api.put(`/cars/${carId}/maintenance`, {
        type: type.trim(),
        interval_km: intervalKm,
        last_service_km: lastKm.trim() ? lastKm.trim() : null,
        note: note.trim() || null,
      });
      setOk(true);
      reload();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="ТО">
      <Async loading={loading} error={error} data={data} empty="ТО не настроено">
        {(rows) => (
          <table>
            <thead>
              <tr><th>Тип</th><th>Интервал, км</th><th>Последнее, км</th><th>Сверх, км</th><th>Когда</th><th></th></tr>
            </thead>
            <tbody>
              {rows.map((m) => (
                <tr key={m.id}>
                  <td>{m.type}</td>
                  <td>{m.interval_km}</td>
                  <td>{m.last_service_km}</td>
                  <td className={m.over_km != null && Number(m.over_km) > 0 ? "overdue" : ""}>{m.over_km ?? "—"}</td>
                  <td>{fmtDate(m.last_service_at)}</td>
                  <td className="row-actions">
                    <button type="button" onClick={() => edit(m)}>Изменить</button>
                    <ActionButton
                      label="Выполнено"
                      confirm="Отметить ТО выполненным сейчас (по текущему пробегу)?"
                      onAction={() => api.post(`/cars/${carId}/maintenance/${encodeURIComponent(m.type)}/done`)}
                      onDone={reload}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Async>
      <form className="form-grid" onSubmit={submit}>
        <label>Тип<input value={type} onChange={(e) => setType(e.target.value)} placeholder="oil" /></label>
        <label>
          Интервал, км
          <input inputMode="numeric" value={intervalKm} onChange={(e) => setIntervalKm(e.target.value)} />
        </label>
        <label>
          Последнее ТО, км
          <input inputMode="numeric" value={lastKm} onChange={(e) => setLastKm(e.target.value)} placeholder="тек. пробег" />
        </label>
        <label>Заметка<input value={note} onChange={(e) => setNote(e.target.value)} /></label>
        <div className="form-actions">
          <button disabled={busy}>{busy ? "…" : "Сохранить ТО"}</button>
          {err && <span className="error">{err}</span>}
          {ok && <span className="info">Сохранено</span>}
        </div>
      </form>
      <p className="hint muted">
        Пустое «Последнее ТО» для нового типа = текущий пробег. Чтобы поправить
        существующий тип, нажмите «Изменить» — иначе база ТО сбросится на «сейчас».
      </p>
    </Card>
  );
}

export default function CarDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
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
            <div className="card-media-row">
              <MediaImage kind="car-photo" refId={c.id} alt={`Фото ${c.plate}`} className="photo" />
              <dl className="kv">
                <dt>Номер</dt><dd>{c.plate}</dd>
                <dt>Модель</dt><dd>{c.model ?? "—"}</dd>
                <dt>Статус</dt><dd>{c.status === "free" ? "свободна" : "занята"}</dd>
                <dt>Водитель</dt>
                <dd>{c.driver_id ? <Link to={`/drivers/${c.driver_id}`}>{c.driver_name}</Link> : "—"}</dd>
              </dl>
            </div>
          )}
        </Async>
        {car.data?.status === "free" && (
          <div className="actions-row">
            <ActionButton
              label="🗑 Удалить машину"
              confirm="Удалить машину? Доступно только для свободной машины без истории."
              danger
              onAction={() => api.del(`/cars/${id}`)}
              onDone={() => navigate("/cars")}
            />
          </div>
        )}
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

      <TrackerCard carId={id!} />
      <MaintenanceCard carId={id!} />

      <Card title="Документы машины">
        <DocumentList path={`/cars/${id}/documents`} />
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
