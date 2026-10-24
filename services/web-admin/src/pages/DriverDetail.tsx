import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { api } from "../lib/api";
import { ActionButton, Async, Card, fmtDate } from "../components/ui";
import { MediaImage } from "../components/Media";
import type { DriverWithStats, Schedule, ScheduleWithStatus } from "../lib/types";

// Конвенция парка: дедлайн оплаты — 22:00 по Бишкеку.
const DUE_TIME_SUFFIX = "T22:00:00+06:00";
const PARK_TZ = "Asia/Bishkek";

// Календарная дата в поясе парка (YYYY-MM-DD). Через UTC нельзя: с 00:00 до
// 06:00 по Бишкеку toISOString() отдал бы вчерашнюю дату, и новый график сразу
// ушёл бы в просрочку.
function parkDate(d: Date): string {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: PARK_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

function ScheduleForm({
  driverId,
  current,
  onSaved,
}: {
  driverId: string;
  current: Schedule | null;
  onSaved: () => void;
}) {
  const [period, setPeriod] = useState(current?.period ?? "daily");
  const [intervalDays, setIntervalDays] = useState(String(current?.interval_days ?? 7));
  const [amount, setAmount] = useState(current?.amount ?? "");
  const today = parkDate(new Date());
  const [dueDate, setDueDate] = useState(
    current?.next_due_date ? parkDate(new Date(current.next_due_date)) : today,
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!(Number(amount) > 0)) {
      setErr("Сумма должна быть больше нуля");
      setOk(false);
      return;
    }
    if (period === "custom" && !(Number(intervalDays) >= 1)) {
      setErr("Интервал — не меньше 1 дня");
      setOk(false);
      return;
    }
    setBusy(true);
    setErr(null);
    setOk(false);
    try {
      await api.put(`/drivers/${driverId}/schedule`, {
        period,
        interval_days: period === "custom" ? Number(intervalDays) : null,
        amount,
        next_due_date: `${dueDate}${DUE_TIME_SUFFIX}`,
      });
      setOk(true);
      onSaved();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form-grid" onSubmit={submit}>
      <label>
        Период
        <select value={period} onChange={(e) => setPeriod(e.target.value)}>
          <option value="daily">ежедневно</option>
          <option value="weekly">еженедельно</option>
          <option value="custom">каждые N дней</option>
        </select>
      </label>
      {period === "custom" && (
        <label>
          Интервал (дней)
          <input type="number" min={1} value={intervalDays} onChange={(e) => setIntervalDays(e.target.value)} />
        </label>
      )}
      <label>
        Сумма
        <input
          inputMode="decimal"
          required
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="1800"
        />
      </label>
      <label>
        След. срок
        <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
      </label>
      <div className="form-actions">
        <button disabled={busy}>{busy ? "…" : "Сохранить график"}</button>
        {err && <span className="error">{err}</span>}
        {ok && <span className="info">Сохранено</span>}
      </div>
    </form>
  );
}

export default function DriverDetail() {
  const { id } = useParams();
  const driver = useApi<DriverWithStats>(`/drivers/${id}`);
  const schedule = useApi<ScheduleWithStatus>(`/drivers/${id}/schedule`);

  return (
    <>
      <p>
        <Link to="/drivers">← К списку</Link>
      </p>
      <h1>{driver.data?.driver.full_name ?? `Водитель ${id}`}</h1>

      <Card title="Карточка">
        <Async loading={driver.loading} error={driver.error} data={driver.data}>
          {(d) => (
            <div className="card-media-row">
              <MediaImage
                kind="driver-selfie"
                refId={d.driver.id}
                alt={`Селфи ${d.driver.full_name}`}
                className="photo"
              />
              <dl className="kv">
                <dt>Телефон</dt><dd>{d.driver.phone ?? "—"}</dd>
                <dt>ИНН</dt><dd>{d.driver.inn ?? "—"}</dd>
                <dt>Машина</dt><dd>{d.driver.car_plate ?? "—"}</dd>
                <dt>Статус</dt><dd>{d.driver.active ? "активен" : "уволен"}</dd>
                <dt>Платежей</dt><dd>{d.stats.payments_count}</dd>
                <dt>Всего оплачено</dt><dd>{d.stats.total_paid}</dd>
              </dl>
            </div>
          )}
        </Async>
        {driver.data?.driver.active && (
          <div className="actions-row">
            <ActionButton
              label="Уволить водителя"
              confirm="Уволить водителя? Он открепится от машины."
              danger
              onAction={() => api.post(`/drivers/${id}/fire`)}
              onDone={driver.reload}
            />
          </div>
        )}
      </Card>

      <Card title="График платежей">
        <Async loading={schedule.loading} error={schedule.error} data={schedule.data}>
          {(s) => (
            <>
              {s.schedule ? (
                <dl className="kv">
                  <dt>Текущий</dt><dd>{s.status?.period_label ?? s.schedule.period}, {s.schedule.amount}</dd>
                  <dt>След. срок</dt><dd>{fmtDate(s.schedule.next_due_date)}</dd>
                  <dt>Состояние</dt>
                  <dd className={s.status?.is_overdue ? "overdue" : ""}>{s.status?.summary ?? "—"}</dd>
                </dl>
              ) : (
                <p className="muted">График не задан.</p>
              )}
              <ScheduleForm driverId={id!} current={s.schedule} onSaved={schedule.reload} />
            </>
          )}
        </Async>
      </Card>
    </>
  );
}
