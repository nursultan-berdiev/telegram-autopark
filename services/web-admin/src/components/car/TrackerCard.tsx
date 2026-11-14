import { useState, type FormEvent } from "react";
import { useApi } from "../../lib/hooks";
import { api } from "../../lib/api";
import { ActionButton, Card, ErrorBox, Loading } from "../ui";
import type { Tracker } from "../../lib/types";

export default function TrackerCard({ carId }: { carId: string }) {
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
