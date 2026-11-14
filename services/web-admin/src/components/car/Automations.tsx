import { Link } from "react-router-dom";
import { useApi } from "../../lib/hooks";
import { scheduleText } from "../../lib/periodic";
import { Async, Card, fmtDate } from "../ui";
import type { CarOverview, PeriodicTask } from "../../lib/types";

// Автоматизации работают по всему парку; здесь — какие из них касаются машины
// и когда запускались. Только показ: настраиваются они в «Настройках».
const noDriver = (c: CarOverview) => (c.driver_id ? null : "нет водителя");
const needsSchedule = (c: CarOverview) =>
  noDriver(c) ?? (c.next_payment_due ? null : "у водителя нет графика оплат");

const RELEVANT: { task: string; title: string; skip: (c: CarOverview) => string | null }[] = [
  { task: "app.tasks.payments_block.block_overdue", title: "Автоблок за неоплату", skip: needsSchedule },
  { task: "app.tasks.payments_unblock.unblock_paid", title: "Снятие блока после оплаты", skip: needsSchedule },
  { task: "app.tasks.fines_tolom.check_fines_tolom", title: "Проверка штрафов (tolom.kg)", skip: () => null },
  { task: "app.tasks.fines.check_fines", title: "Проверка штрафов (carcheck.gov.kg)", skip: () => null },
  { task: "app.tasks.fines_enforcement.enforce_fines", title: "Блокировка за штрафы", skip: () => null },
  { task: "app.tasks.photo_inspections.notify_photo_inspections", title: "Фотоконтроль: напоминания", skip: noDriver },
];

export default function Automations({ car }: { car: CarOverview }) {
  const tasks = useApi<PeriodicTask[]>("/periodic-tasks");
  return (
    <Card title="Что действует на эту машину">
      <Async loading={tasks.loading} error={tasks.error} data={tasks.data}>
        {(all) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Автоматизация</th><th>Расписание</th><th>Статус</th><th>Последний запуск</th><th>К этой машине</th></tr>
              </thead>
              <tbody>
                {RELEVANT.flatMap((r) => {
                  const why = r.skip(car);
                  const applies = why ? <span className="tone-muted">нет: {why}</span> : <span className="tone-ok">да</span>;
                  const found = all.filter((t) => t.task === r.task);
                  if (!found.length) {
                    return [
                      <tr key={r.task}>
                        <td>{r.title}</td>
                        <td className="muted" colSpan={3}>не настроена</td>
                        <td>{applies}</td>
                      </tr>,
                    ];
                  }
                  return found.map((t) => (
                    <tr key={t.id}>
                      <td>
                        {r.title}
                        <div className="muted line-sub">{t.name}</div>
                      </td>
                      <td>{scheduleText(t)}</td>
                      <td className={t.enabled ? "tone-ok" : "tone-muted"}>{t.enabled ? "включена" : "выключена"}</td>
                      <td>{fmtDate(t.last_run_at)}</td>
                      <td>{applies}</td>
                    </tr>
                  ));
                })}
              </tbody>
            </table>
          </div>
        )}
      </Async>
      <p className="hint muted">
        Автоматизации общие для всего парка, менять их — в <Link to="/settings?tab=automations">Настройках</Link>.
      </p>
    </Card>
  );
}
