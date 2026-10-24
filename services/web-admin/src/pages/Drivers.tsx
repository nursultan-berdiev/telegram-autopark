import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { Async } from "../components/ui";
import type { Driver } from "../lib/types";

export default function Drivers() {
  const [active, setActive] = useState(true);
  const { data, error, loading } = useApi<Driver[]>(`/drivers?active=${active}`);
  return (
    <>
      <h1>Водители</h1>
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
