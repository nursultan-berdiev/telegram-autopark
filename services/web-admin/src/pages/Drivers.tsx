import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { api } from "../lib/api";
import { Async, Card } from "../components/ui";
import type { Car, Driver, Invitation } from "../lib/types";

function InviteDriver() {
  const freeCars = useApi<Car[]>("/cars?free=true");
  const [carId, setCarId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [invite, setInvite] = useState<Invitation | null>(null);

  const create = async () => {
    if (!carId) return;
    setBusy(true);
    setErr(null);
    setInvite(null);
    try {
      const inv = await api.post<Invitation>("/invitations", { car_id: Number(carId) });
      setInvite(inv);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Новый водитель">
      <Async loading={freeCars.loading} error={freeCars.error} data={freeCars.data} empty="Свободных машин нет">
        {(cars) => (
          <div className="form-row">
            <select value={carId} onChange={(e) => setCarId(e.target.value)}>
              <option value="">— выберите свободную машину —</option>
              {cars.map((c) => (
                <option key={c.id} value={c.id}>{c.plate}{c.model ? ` (${c.model})` : ""}</option>
              ))}
            </select>
            <button disabled={busy || !carId} onClick={create}>{busy ? "…" : "Создать приглашение"}</button>
            {err && <span className="error">{err}</span>}
          </div>
        )}
      </Async>
      {invite && (
        <p className="invite">
          Код приглашения: <code>{invite.code}</code> (действует {invite.ttl_label}). Передайте водителю — он
          вводит код в боте при регистрации.
        </p>
      )}
    </Card>
  );
}

export default function Drivers() {
  const [active, setActive] = useState(true);
  const { data, error, loading } = useApi<Driver[]>(`/drivers?active=${active}`);
  return (
    <>
      <h1>Водители</h1>
      <InviteDriver />
      <div className="tabs">
        <button className={active ? "active" : ""} onClick={() => setActive(true)}>Активные</button>
        <button className={!active ? "active" : ""} onClick={() => setActive(false)}>Уволенные</button>
      </div>
      <Async loading={loading} error={error} data={data} empty="Нет водителей">
        {(rows) => (
          <table>
            <thead>
              <tr><th>Имя</th><th>Телефон</th><th>Машина</th></tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.id}>
                  <td><Link to={`/drivers/${d.id}`}>{d.full_name}</Link></td>
                  <td>{d.phone ?? "—"}</td>
                  <td>{d.car_plate ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Async>
    </>
  );
}
