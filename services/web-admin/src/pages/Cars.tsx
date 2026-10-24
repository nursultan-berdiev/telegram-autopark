import { Link } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { Async } from "../components/ui";
import type { Car } from "../lib/types";

export default function Cars() {
  const { data, error, loading } = useApi<Car[]>("/cars");
  return (
    <>
      <h1>Автопарк</h1>
      <Async loading={loading} error={error} data={data} empty="Машин нет">
        {(cars) => (
          <table>
            <thead>
              <tr>
                <th>Номер</th>
                <th>Модель</th>
                <th>Статус</th>
                <th>Водитель</th>
              </tr>
            </thead>
            <tbody>
              {cars.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link to={`/cars/${c.id}`}>{c.plate}</Link>
                  </td>
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
