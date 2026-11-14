import { useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { Card } from "./ui";

export default function AddCarForm({ onAdded }: { onAdded: () => void }) {
  const [plate, setPlate] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!plate.trim()) {
      setErr("Укажите гос. номер");
      return;
    }
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
      <form className="form-grid" onSubmit={submit} noValidate>
        <label>
          Гос. номер
          <input value={plate} onChange={(e) => setPlate(e.target.value)} placeholder="01KG123ABC" />
        </label>
        <label>
          Марка и модель
          <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="необязательно" />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "…" : "Добавить"}
        </button>
        {err && <span className="error">{err}</span>}
      </form>
      <p className="muted hint">Фото машины добавляется через бота (в вебе пока только текст).</p>
    </Card>
  );
}
