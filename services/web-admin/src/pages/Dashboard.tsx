import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { Async } from "../components/ui";
import { CarPhoto, ToneLine } from "../components/CarPhoto";
import AddCarForm from "../components/AddCarForm";
import {
  engine,
  inspectionDue,
  lastInspection,
  lastPayment,
  paymentDue,
} from "../lib/carStatus";
import type { CarOverview } from "../lib/types";

function CarCard({ c }: { c: CarOverview }) {
  return (
    <article className="car-card">
      <Link to={`/cars/${c.id}`} className="car-photo" tabIndex={-1}>
        <CarPhoto id={c.id} plate={c.plate} hasPhoto={c.has_photo} />
      </Link>
      <div className="car-body">
        <div className="car-head">
          <div className="car-title">
            <div className="car-plate">
              <span className={`tracker-dot${c.online ? " on" : ""}`} title={c.online ? "Трекер на связи" : "Трекер не на связи"} />
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
          <dd>{c.driver_id ? <Link to={`/drivers/${c.driver_id}`}>{c.driver_name ?? "водитель без имени"}</Link> : <span className="tone-muted">свободна</span>}</dd>
          <dt>Оплата</dt>
          <dd>
            <ToneLine line={lastPayment(c)} />
            <ToneLine line={paymentDue(c)} sub />
          </dd>
          <dt>Двигатель</dt>
          <dd>
            <ToneLine line={engine(c)} />
          </dd>
          <dt>Фотоконтроль</dt>
          <dd>
            <ToneLine line={lastInspection(c)} />
            <ToneLine line={inspectionDue(c)} sub />
          </dd>
        </dl>
        <Link className="btn-soft car-more" to={`/cars/${c.id}`}>
          Подробнее
        </Link>
      </div>
    </article>
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
          <div className="car-grid">
            {cars.map((c) => (
              <CarCard key={c.id} c={c} />
            ))}
          </div>
        )}
      </Async>
    </>
  );
}
