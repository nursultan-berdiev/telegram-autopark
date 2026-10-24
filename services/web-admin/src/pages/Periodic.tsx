import { useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { ActionButton, Async, Card, fmtDate } from "../components/ui";
import type { PeriodicTask } from "../lib/types";

function scheduleText(t: PeriodicTask): string {
  if (t.crontab) return `crontab: ${t.crontab}`;
  const s = t.interval_seconds;
  if (s == null) return "—";
  if (s % 3600 === 0) return `каждые ${s / 3600} ч`;
  if (s % 60 === 0) return `каждые ${s / 60} мин`;
  return `каждые ${s} с`;
}

type PeriodType = "interval" | "crontab";

function NewTask({ catalog, onDone }: { catalog: Record<string, string>; onDone: () => void }) {
  const [name, setName] = useState("");
  const [task, setTask] = useState("");
  const [period, setPeriod] = useState<PeriodType>("interval");
  // Не interval: имя затеняло бы глобальный window.setInterval.
  const [intervalSeconds, setIntervalSeconds] = useState("3600");
  const [crontab, setCrontab] = useState("0 9 * * *");
  const [enabled, setEnabled] = useState(true);

  // Сервер (validate_period) отвергает интервал < 60 и дробный — гейтим так же,
  // чтобы кнопка не была активна на заведомо отказном вводе.
  const n = Number(intervalSeconds);
  const intervalOk = period === "interval" && Number.isInteger(n) && n >= 60;
  const crontabOk = period === "crontab" && crontab.trim().length > 0;
  const canCreate = name.trim().length > 0 && task.length > 0 && (intervalOk || crontabOk);

  const create = async () => {
    await api.post<PeriodicTask>("/periodic-tasks", {
      name: name.trim(),
      task,
      enabled,
      ...(period === "interval"
        ? { interval_seconds: Number(intervalSeconds) }
        : { crontab: crontab.trim() }),
    });
    setName("");
    setTask("");
    return "Расписание создано";
  };

  return (
    <Card title="Новое расписание">
      <div className="task-form">
        <label>Название</label>
        <input className="field" value={name} onChange={(e) => setName(e.target.value)} />

        <label>Задача</label>
        <select className="field" value={task} onChange={(e) => setTask(e.target.value)}>
          <option value="">— выберите —</option>
          {Object.entries(catalog).map(([key, label]) => (
            <option key={key} value={key}>{label}</option>
          ))}
        </select>

        <label>Период</label>
        <div className="period-row">
          <label className="radio">
            <input
              type="radio"
              checked={period === "interval"}
              onChange={() => setPeriod("interval")}
            />{" "}
            интервал, с
          </label>
          <input
            className="field field-sm"
            type="number"
            min={60}
            step={1}
            value={intervalSeconds}
            disabled={period !== "interval"}
            onChange={(e) => setIntervalSeconds(e.target.value)}
          />
          <label className="radio">
            <input
              type="radio"
              checked={period === "crontab"}
              onChange={() => setPeriod("crontab")}
            />{" "}
            crontab
          </label>
          <input
            className="field field-sm"
            value={crontab}
            disabled={period !== "crontab"}
            onChange={(e) => setCrontab(e.target.value)}
          />
        </div>

        <label>Включить</label>
        <label className="radio">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />{" "}
          запускать по расписанию
        </label>
      </div>
      <div className="actions-row">
        {canCreate ? (
          <ActionButton label="Создать" onAction={create} onDone={onDone} />
        ) : (
          <button disabled>Создать</button>
        )}
      </div>
    </Card>
  );
}

export default function Periodic() {
  const tasks = useApi<PeriodicTask[]>("/periodic-tasks");
  const catalog = useApi<Record<string, string>>("/periodic-tasks/catalog");
  const labels = catalog.data ?? {};

  return (
    <>
      <h1>Периодические задачи</h1>

      {catalog.data && <NewTask catalog={catalog.data} onDone={tasks.reload} />}

      <Card title="Расписания">
        <Async loading={tasks.loading} error={tasks.error} data={tasks.data} empty="Расписаний нет">
          {(rows) => (
            <table>
              <thead>
                <tr>
                  <th>Название</th><th>Задача</th><th>Период</th><th>Статус</th>
                  <th>Последний запуск</th><th>Прогонов</th><th></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={t.id}>
                    <td>{t.name}</td>
                    <td>{labels[t.task] ?? t.task}</td>
                    <td>{scheduleText(t)}</td>
                    <td>{t.enabled ? "вкл" : "выкл"}</td>
                    <td>{fmtDate(t.last_run_at)}</td>
                    <td>{t.total_run_count}</td>
                    <td className="row-actions">
                      <ActionButton
                        label="Запустить"
                        confirm="Поставить задачу в очередь сейчас?"
                        onAction={async () => {
                          await api.post(`/periodic-tasks/${t.id}/run`);
                          return "В очереди";
                        }}
                      />
                      <ActionButton
                        label={t.enabled ? "Выключить" : "Включить"}
                        onAction={() => api.patch(`/periodic-tasks/${t.id}`, { enabled: !t.enabled })}
                        onDone={tasks.reload}
                      />
                      <ActionButton
                        label="Удалить"
                        confirm="Удалить расписание?"
                        danger
                        onAction={() => api.del(`/periodic-tasks/${t.id}`)}
                        onDone={tasks.reload}
                      />
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
