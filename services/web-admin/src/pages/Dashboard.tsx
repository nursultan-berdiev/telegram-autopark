import { useApi } from "../lib/hooks";
import { Async, Card } from "../components/ui";
import type { Alert, Car, Driver } from "../lib/types";

function Stat({ label, value, loading }: { label: string; value?: number; loading: boolean }) {
  return (
    <div className="stat">
      <div className="stat-value">{loading ? "…" : (value ?? "—")}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function alertText(a: Alert): string {
  const t = a.payload && typeof a.payload.text === "string" ? a.payload.text : "";
  return t || a.type;
}

export default function Dashboard() {
  const cars = useApi<Car[]>("/cars");
  const drivers = useApi<Driver[]>("/drivers?active=true");
  const alerts = useApi<Alert[]>("/alerts?status=open");

  return (
    <>
      <h1>Дашборд</h1>
      <div className="stat-row">
        <Stat label="Машины" value={cars.data?.length} loading={cars.loading} />
        <Stat label="Активные водители" value={drivers.data?.length} loading={drivers.loading} />
        <Stat label="Открытые алерты" value={alerts.data?.length} loading={alerts.loading} />
      </div>
      <Card title="Последние открытые алерты">
        <Async loading={alerts.loading} error={alerts.error} data={alerts.data} empty="Алертов нет">
          {(rows) => (
            <ul className="plain">
              {rows.slice(0, 10).map((a) => (
                <li key={a.id}>
                  <span className={`badge ${a.severity}`}>{a.severity}</span> {alertText(a)}
                </li>
              ))}
            </ul>
          )}
        </Async>
      </Card>
    </>
  );
}
