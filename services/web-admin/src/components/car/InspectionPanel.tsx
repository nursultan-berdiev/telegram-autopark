import { Link } from "react-router-dom";
import { Card, fmtDate } from "../ui";
import { ToneLine } from "../CarPhoto";
import { inspectionDue } from "../../lib/carStatus";
import type { CarOverview, PhotoInspection } from "../../lib/types";

// Плановый осмотр: когда был, когда следующий и все осмотры машины. Клик по
// осмотру показывает его снимки в галерее, «альбом» — отдельную страницу осмотра.
export default function InspectionPanel({
  car,
  list,
  selectedId,
  onSelect,
}: {
  car: CarOverview;
  list: PhotoInspection[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  const last = list[0] ?? null;
  return (
    <Card title="Плановый осмотр (фотоконтроль)">
      <dl className="facts-list">
        <dt>Последний</dt>
        <dd>
          {last ? (
            <>
              {fmtDate(last.created_at)}
              <span className="muted"> · {last.driver_name ?? "водитель удалён"}</span>
              {last.has_issues && <span className="chip bad insp-issues">есть замечания</span>}
            </>
          ) : (
            <span className="tone-muted">ещё не было</span>
          )}
        </dd>
        <dt>Следующий</dt>
        <dd>
          <ToneLine line={inspectionDue(car)} />
        </dd>
      </dl>
      {last?.comment && <p className="insp-comment">Замечание водителя: {last.comment}</p>}
      {list.length > 0 && (
        <div className="insp-history">
          {list.map((i) => (
            <div key={i.id} className={`insp-row${i.id === selectedId ? " on" : ""}`}>
              <button type="button" className="insp-row-pick" aria-pressed={i.id === selectedId} onClick={() => onSelect(i.id)}>
                <b>{fmtDate(i.created_at)}</b>
                <span className="muted"> · {i.driver_name ?? "—"}</span>
                {i.has_issues && <span className="chip bad insp-issues">замечания</span>}
              </button>
              <Link className="insp-row-album" to={`/photo-inspections/${i.id}`}>
                альбом
              </Link>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
