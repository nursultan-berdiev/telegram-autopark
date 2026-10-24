import { Link, useParams } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { Async, Card, fmtDate } from "../components/ui";
import type { DriverWithStats, ScheduleWithStatus } from "../lib/types";

export default function DriverDetail() {
  const { id } = useParams();
  const driver = useApi<DriverWithStats>(`/drivers/${id}`);
  const schedule = useApi<ScheduleWithStatus>(`/drivers/${id}/schedule`);

  return (
    <>
      <p><Link to="/drivers">← К списку</Link></p>
      <h1>{driver.data?.driver.full_name ?? `Водитель ${id}`}</h1>

      <Card title="Карточка">
        <Async loading={driver.loading} error={driver.error} data={driver.data}>
          {(d) => (
            <dl className="kv">
              <dt>Телефон</dt><dd>{d.driver.phone ?? "—"}</dd>
              <dt>ИНН</dt><dd>{d.driver.inn ?? "—"}</dd>
              <dt>Машина</dt><dd>{d.driver.car_plate ?? "—"}</dd>
              <dt>Статус</dt><dd>{d.driver.active ? "активен" : "уволен"}</dd>
              <dt>Платежей</dt><dd>{d.stats.payments_count}</dd>
              <dt>Всего оплачено</dt><dd>{d.stats.total_paid}</dd>
            </dl>
          )}
        </Async>
      </Card>

      <Card title="График платежей">
        <Async loading={schedule.loading} error={schedule.error} data={schedule.data} empty="График не задан">
          {(s) =>
            s.schedule ? (
              <dl className="kv">
                <dt>Период</dt><dd>{s.status?.period_label ?? s.schedule.period}</dd>
                <dt>Сумма</dt><dd>{s.schedule.amount}</dd>
                <dt>След. платёж</dt><dd>{fmtDate(s.schedule.next_due_date)}</dd>
                <dt>Состояние</dt>
                <dd className={s.status?.is_overdue ? "overdue" : ""}>{s.status?.summary ?? "—"}</dd>
              </dl>
            ) : (
              <div className="muted">График не задан</div>
            )
          }
        </Async>
      </Card>
    </>
  );
}
