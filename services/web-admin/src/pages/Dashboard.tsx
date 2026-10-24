import { useApi } from "../lib/hooks";
import { Async } from "../components/ui";
import type { Alert, Car, Driver } from "../lib/types";

// Градиентная карточка-счётчик (hero).
function HeroCard({
  tone,
  icon,
  value,
  label,
  sub,
  loading,
}: {
  tone: "i" | "t" | "a" | "r";
  icon: string;
  value?: number;
  label: string;
  sub?: string;
  loading: boolean;
}) {
  return (
    <div className={`sc ${tone}`}>
      <div className="ic" aria-hidden>
        {icon}
      </div>
      <div className="n">{loading ? "…" : (value ?? "—")}</div>
      <div className="l">{label}</div>
      {sub && <div className="s">{sub}</div>}
    </div>
  );
}

function alertText(a: Alert): string {
  const t = a.payload && typeof a.payload.text === "string" ? a.payload.text : "";
  return t || a.type;
}

// critical/error/warning → «!», info → «i»; класс для цветной плашки.
function sevClass(sev: string): "critical" | "warning" | "info" {
  if (sev === "warning") return "warning";
  if (sev === "info") return "info";
  return "critical";
}

// Короткое «сколько назад» без внешних библиотек.
function ago(iso: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const m = Math.floor(Math.max(0, Date.now() - t) / 60000);
  if (m < 1) return "только что";
  if (m < 60) return `${m} мин`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} ч`;
  return `${Math.floor(h / 24)} дн`;
}

export default function Dashboard() {
  const cars = useApi<Car[]>("/cars");
  const drivers = useApi<Driver[]>("/drivers?active=true");
  const alerts = useApi<Alert[]>("/alerts?status=open");

  const carList = cars.data ?? [];
  const free = carList.filter((c) => c.status === "free").length;
  const occupied = carList.length - free;
  const atWheel = (drivers.data ?? []).filter((d) => d.car_id != null).length;
  const alertRows = alerts.data ?? [];
  const crit = alertRows.filter((a) => sevClass(a.severity) === "critical").length;
  const warn = alertRows.filter((a) => sevClass(a.severity) === "warning").length;

  const plateOf = (carId: number) => carList.find((c) => c.id === carId)?.plate ?? `#${carId}`;

  const updated = new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

  return (
    <div className="dash">
      <div className="head">
        <h1>Дашборд</h1>
        <div className="u">Обновлено {updated}</div>
      </div>

      <section className="hero">
        <HeroCard
          tone="i"
          icon="▦"
          value={cars.data?.length}
          label="Машины"
          sub={cars.data ? `${occupied} заняты · ${free} свободны` : undefined}
          loading={cars.loading}
        />
        <HeroCard
          tone="t"
          icon="●"
          value={drivers.data?.length}
          label="Активные водители"
          sub={drivers.data ? `${atWheel} за рулём` : undefined}
          loading={drivers.loading}
        />
        <HeroCard
          tone="r"
          icon="⚠"
          value={alerts.data?.length}
          label="Открытые алерты"
          sub={alerts.data ? `${crit} критичных · ${warn} внимание` : undefined}
          loading={alerts.loading}
        />
      </section>

      <div className="grid">
        <section className="panel">
          <h2>Открытые алерты</h2>
          <div className="csub">Требуют внимания прямо сейчас</div>
          <Async loading={alerts.loading} error={alerts.error} data={alerts.data} empty="Алертов нет">
            {(rows) => (
              <div>
                {rows.slice(0, 10).map((a) => (
                  <div className={`al ${sevClass(a.severity)}`} key={a.id}>
                    <div className="ic">{sevClass(a.severity) === "info" ? "i" : "!"}</div>
                    <div className="al-body">
                      <div className="t">{alertText(a)}</div>
                      <div className="c">{plateOf(a.car_id)}</div>
                    </div>
                    <div className="ago">{ago(a.triggered_at)}</div>
                  </div>
                ))}
              </div>
            )}
          </Async>
        </section>

        <section className="panel">
          <h2>Парк сейчас</h2>
          <div className="csub">Статус по каждой машине</div>
          <Async loading={cars.loading} error={cars.error} data={cars.data} empty="Машин нет">
            {(rows) => (
              <div className="fleet">
                {rows.map((c) => (
                  <div className="fl" key={c.id}>
                    <span className={`st ${c.status === "free" ? "free" : "idle"}`} />
                    <div className="fl-body">
                      <div className="p">{c.plate}</div>
                      <div className="mo">
                        {c.model ?? "—"}
                        {c.driver_name ? ` · ${c.driver_name}` : ""}
                      </div>
                    </div>
                    <div className="fl-right">{c.status === "free" ? "свободна" : "занята"}</div>
                  </div>
                ))}
              </div>
            )}
          </Async>
        </section>
      </div>
    </div>
  );
}
