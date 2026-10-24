import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { api } from "../lib/api";
import { Async, Card } from "../components/ui";
import type { Car } from "../lib/types";

function AddCar({ onAdded }: { onAdded: () => void }) {
  const [plate, setPlate] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!plate.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await api.post("/cars", { plate: plate.trim(), model: model.trim() || null });
      setPlate("");
      setModel("");
      onAdded();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Добавить машину">
      <form className="form-row" onSubmit={submit}>
        <input placeholder="Гос. номер" value={plate} onChange={(e) => setPlate(e.target.value)} />
        <input placeholder="Модель (необязательно)" value={model} onChange={(e) => setModel(e.target.value)} />
        <button disabled={busy}>{busy ? "…" : "Добавить"}</button>
        {err && <span className="error">{err}</span>}
      </form>
      <p className="muted hint">Фото машины добавляется через бота (в вебе пока только текст).</p>
    </Card>
  );
}

export default function Cars() {
  const { data, error, loading, reload } = useApi<Car[]>("/cars");
  return (
    <>
      <h1>Автопарк</h1>
      <AddCar onAdded={reload} />
      <Async loading={loading} error={error} data={data} empty="Машин нет">
        {(cars) => (
          <table>
            <thead>
              <tr><th>Номер</th><th>Модель</th><th>Статус</th><th>Водитель</th></tr>
            </thead>
            <tbody>
              {cars.map((c) => (
                <tr key={c.id}>
                  <td><Link to={`/cars/${c.id}`}>{c.plate}</Link></td>
                  <td>{c.model ?? "—"}</td>
                  <td>{c.status === "free" ? "свободна" : "занята"}</td>
                  <td>{c.driver_name ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Async>
    </>
  );
}
