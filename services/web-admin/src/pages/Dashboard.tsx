import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { Async } from "../components/ui";
import { MediaImage } from "../components/Media";
import AddCarForm from "../components/AddCarForm";
import {
  engine,
  inspectionDue,
  isBlocked,
  lastInspection,
  lastPayment,
  paymentDue,
  type Line,
} from "../lib/carStatus";
import type { CarOverview } from "../lib/types";

function Tone({ line, sub }: { line: Line | null; sub?: boolean }) {
  if (!line) return null;
  return <div className={`tone-${line.tone}${sub ? " sub" : ""}`}>{line.text}</div>;
}

function PhotoStub({ plate }: { plate: string }) {
  return (
    <div className="car-photo-stub" aria-label={`Фото ${plate} нет`}>
      <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 11l1.5-4.5A2 2 0 0 1 8.4 5h7.2a2 2 0 0 1 1.9 1.5L19 11v5H5z" />
        <circle cx="7.5" cy="16" r="1.3" />
        <circle cx="16.5" cy="16" r="1.3" />
      </svg>
      <span>Фото нет</span>
    </div>
  );
}

function CarCard({ c }: { c: CarOverview }) {
  return (
    <article className="car-card">
      <Link to={`/cars/${c.id}`} className="car-photo" tabIndex={-1}>
        {c.has_photo ? (
          <MediaImage kind="car-photo" refId={c.id} alt={`Фото ${c.plate}`} className="car-photo-img" fallback={<PhotoStub plate={c.plate} />} />
        ) : (
          <PhotoStub plate={c.plate} />
        )}
      </Link>
      <div className="car-body">
        <div className="car-head">
          <div className="car-title">
            <div className="car-plate">
              <span className={`net${c.online ? " on" : ""}`} title={c.online ? "Трекер на связи" : "Трекер не на связи"} />
              {c.plate}
            </div>
            <div className="car-model">{c.model ?? "марка не указана"}</div>
          </div>
          <div className="car-badges">
            {c.unpaid_fines > 0 && <span className="chip bad">Штрафы: {c.unpaid_fines}</span>}
            {c.open_alerts > 0 && <span className="chip warn">Алерты: {c.open_alerts}</span>}
          </div>
        </div>
        <dl className="car-facts">
          <dt>Водитель</dt>
          <dd>{c.driver_id ? <Link to={`/drivers/${c.driver_id}`}>{c.driver_name}</Link> : <span className="tone-muted">свободна</span>}</dd>
          <dt>Оплата</dt>
          <dd>
            <Tone line={lastPayment(c)} />
            <Tone line={paymentDue(c)} sub />
          </dd>
          <dt>Двигатель</dt>
          <dd>
            <Tone line={engine(c)} />
          </dd>
          <dt>Фотоконтроль</dt>
          <dd>
            <Tone line={lastInspection(c)} />
            <Tone line={inspectionDue(c)} sub />
          </dd>
        </dl>
        <Link className="btn car-more" to={`/cars/${c.id}`}>
          Подробнее
        </Link>
      </div>
    </article>
  );
}

// Узкая строка над карточками: сколько машин и что требует внимания.
function Summary({ cars }: { cars: CarOverview[] }) {
  const free = cars.filter((c) => !c.driver_id).length;
  const blocked = cars.filter(isBlocked).length;
  const latePay = cars.filter((c) => c.payment_overdue_days > 0).length;
  const lateFoto = cars.filter((c) => c.inspection_overdue_days > 0).length;
  const fines = cars.reduce((n, c) => n + c.unpaid_fines, 0);
  const alerts = cars.reduce((n, c) => n + c.open_alerts, 0);
  return (
    <div className="summary">
      <span className="chip">Машин: {cars.length}</span>
      <span className="chip">Свободных: {free}</span>
      <span className={`chip${blocked ? " bad" : ""}`}>Заблокировано: {blocked}</span>
      <span className={`chip${latePay ? " bad" : ""}`}>Просрочка оплаты: {latePay}</span>
      <span className={`chip${lateFoto ? " warn" : ""}`}>Фотоконтроль просрочен: {lateFoto}</span>
      <span className={`chip${fines ? " bad" : ""}`}>Неоплаченных штрафов: {fines}</span>
      <Link className={`chip${alerts ? " warn" : ""}`} to="/journals?tab=alerts">
        Открытых алертов: {alerts}
      </Link>
    </div>
  );
}

export default function Dashboard() {
  const overview = useApi<CarOverview[]>("/cars/overview");
  const [adding, setAdding] = useState(false);

  return (
    <>
      <div className="cars-head">
        <h1>Машины</h1>
        <button onClick={() => setAdding((a) => !a)}>{adding ? "Скрыть" : "+ Добавить машину"}</button>
      </div>
      {adding && (
        <AddCarForm
          onAdded={() => {
            setAdding(false);
            overview.reload();
          }}
        />
      )}
      <Async loading={overview.loading} error={overview.error} data={overview.data} empty="Машин пока нет">
        {(cars) => (
          <>
            <Summary cars={cars} />
            <div className="car-grid">
              {cars.map((c) => (
                <CarCard key={c.id} c={c} />
              ))}
            </div>
          </>
        )}
      </Async>
    </>
  );
}
