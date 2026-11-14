import { MediaImage } from "./Media";
import type { Line } from "../lib/carStatus";

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

// Фото машины или заглушка; растягивается на контейнер (position: relative).
export function CarPhoto({ id, plate, hasPhoto }: { id: number; plate: string; hasPhoto: boolean }) {
  if (!hasPhoto) return <PhotoStub plate={plate} />;
  return (
    <MediaImage kind="car-photo" refId={id} alt={`Фото ${plate}`} className="car-photo-img" fallback={<PhotoStub plate={plate} />} />
  );
}

// Строка подписи с цветом из lib/carStatus.
export function ToneLine({ line, sub }: { line: Line | null; sub?: boolean }) {
  if (!line) return null;
  return <div className={`tone-${line.tone}${sub ? " line-sub" : ""}`}>{line.text}</div>;
}
