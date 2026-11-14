import { useState, type FormEvent } from "react";
import { useApi } from "../../lib/hooks";
import { api } from "../../lib/api";
import { ActionButton, Async, Card, fmtDate } from "../ui";
import type { Maintenance } from "../../lib/types";

export default function MaintenanceCard({ carId }: { carId: string }) {
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
