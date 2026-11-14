import { Link, useParams } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { Async, Card, fmtDate } from "../components/ui";
import { MediaImage } from "../components/Media";
import { INSPECTION_ANGLES as ANGLES } from "../lib/inspection";
import type { PhotoInspection } from "../lib/types";

export default function InspectionAlbum() {
  const { id } = useParams();
  const insp = useApi<PhotoInspection>(`/photo-inspections/${id}`);
  return (
    <>
      <p>
        <Link to={insp.data?.car_id ? `/cars/${insp.data.car_id}` : "/"}>← К машине</Link>
      </p>
      <h1>Осмотр №{id}</h1>
      <Async loading={insp.loading} error={insp.error} data={insp.data}>
        {(i) => (
          <>
            <Card title="Осмотр">
              <dl className="kv">
                <dt>Водитель</dt>
                <dd>
                  {i.driver_name ? <Link to={`/drivers/${i.driver_id}`}>{i.driver_name}</Link> : "—"}
                </dd>
                <dt>Машина</dt>
                <dd>{i.car_id ? <Link to={`/cars/${i.car_id}`}>{i.car_plate}</Link> : "—"}</dd>
                <dt>Когда</dt>
                <dd>{fmtDate(i.created_at)}</dd>
                <dt>Замечания</dt>
                <dd className={i.has_issues ? "overdue" : ""}>
                  {i.has_issues ? <span className="badge error">есть замечания</span> : "нет"}
                </dd>
                {i.comment && (
                  <>
                    <dt>Комментарий</dt>
                    <dd>{i.comment}</dd>
                  </>
                )}
              </dl>
            </Card>

            <Card title="Фото по ракурсам">
              <div className="album">
                {ANGLES.map((a) => {
                  const has = i[a.field] != null;
                  return (
                    <figure className="shot" key={a.key}>
                      {has ? (
                        <a
                          href={`/api/media/inspection/${i.id}?angle=${a.key}`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <MediaImage
                            kind="inspection"
                            refId={i.id}
                            angle={a.key}
                            alt={a.label}
                            className="shot-img"
                            fallback={<span className="shot-empty">недоступно</span>}
                          />
                        </a>
                      ) : (
                        <span className="shot-empty">нет фото</span>
                      )}
                      <figcaption>{a.label}</figcaption>
                    </figure>
                  );
                })}
              </div>
            </Card>
          </>
        )}
      </Async>
    </>
  );
}
