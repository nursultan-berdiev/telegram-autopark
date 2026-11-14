import { useEffect, useState } from "react";
import { MediaImage } from "../Media";
import { CarPhoto } from "../CarPhoto";
import { INSPECTION_ANGLES } from "../../lib/inspection";
import { shortDate } from "../../lib/carStatus";
import type { CarOverview, PhotoInspection } from "../../lib/types";

interface Shot {
  key: string;
  label: string;
  kind: "car-photo" | "inspection";
  refId: number;
  angle?: string;
}

function mediaUrl(s: Shot): string {
  return `/api/media/${s.kind}/${s.refId}${s.angle ? `?angle=${s.angle}` : ""}`;
}

// Фото машины и снимки выбранного осмотра: большое фото и лента миниатюр.
// jump — осмотр выбрали вручную: сразу показать его снимки, а не фото машины.
export default function Gallery({
  car,
  inspection,
  jump,
}: {
  car: CarOverview;
  inspection: PhotoInspection | null;
  jump: boolean;
}) {
  const shots: Shot[] = [];
  if (car.has_photo) shots.push({ key: "car", label: "Фото машины", kind: "car-photo", refId: car.id });
  if (inspection) {
    for (const a of INSPECTION_ANGLES) {
      if (!inspection[a.field]) continue; // у старых осмотров нет боков
      shots.push({
        key: a.key,
        label: `Осмотр ${shortDate(inspection.created_at)} · ${a.label}`,
        kind: "inspection",
        refId: inspection.id,
        angle: a.key,
      });
    }
  }
  const [picked, setPicked] = useState(0);
  useEffect(() => setPicked(jump && car.has_photo ? 1 : 0), [inspection?.id, jump, car.has_photo]);

  if (!shots.length) {
    return (
      <div className="gallery-main">
        <CarPhoto id={car.id} plate={car.plate} hasPhoto={false} />
      </div>
    );
  }
  const main = shots[Math.min(picked, shots.length - 1)];
  return (
    <div className="gallery">
      <a className="gallery-main" href={mediaUrl(main)} target="_blank" rel="noopener noreferrer" title="Открыть в полном размере">
        <MediaImage
          key={mediaUrl(main)}
          kind={main.kind}
          refId={main.refId}
          angle={main.angle}
          alt={main.label}
          className="gallery-img"
          fallback={<div className="gallery-empty">Снимок не загрузился</div>}
        />
        <span className="gallery-cap">{main.label}</span>
      </a>
      {shots.length > 1 && (
        <div className="gallery-thumbs">
          {shots.map((s, i) => (
            <button
              key={s.key}
              type="button"
              className={`gallery-thumb${s === main ? " on" : ""}`}
              aria-label={s.label}
              aria-pressed={s === main}
              onClick={() => setPicked(i)}
            >
              <MediaImage kind={s.kind} refId={s.refId} angle={s.angle} alt="" className="gallery-thumb-img" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
