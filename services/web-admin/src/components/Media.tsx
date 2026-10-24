import { useState, type ReactNode } from "react";

// Картинка из медиа-прокси core-api (/api/media/{kind}/{ref}). Кука-сессия
// уходит автоматически (same-origin). Нет файла (404) или это не картинка —
// показываем fallback, не ломая вёрстку; при смене ref сбрасываем ошибку.
export function MediaImage({
  kind,
  refId,
  angle,
  alt,
  className,
  fallback,
}: {
  kind: "car-photo" | "driver-selfie" | "document" | "inspection";
  refId: number | string;
  angle?: string;
  alt: string;
  className?: string;
  fallback?: ReactNode;
}) {
  const src = `/api/media/${kind}/${refId}${angle ? `?angle=${encodeURIComponent(angle)}` : ""}`;
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  if (failedSrc === src) return <>{fallback ?? null}</>;
  return (
    <img
      className={className ?? "media-img"}
      src={src}
      alt={alt}
      loading="lazy"
      onError={() => setFailedSrc(src)}
    />
  );
}
