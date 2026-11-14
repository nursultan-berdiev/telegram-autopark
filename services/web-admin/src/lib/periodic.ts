import type { PeriodicTask } from "./types";

export function scheduleText(t: PeriodicTask): string {
  if (t.crontab) return `crontab: ${t.crontab}`;
  const s = t.interval_seconds;
  if (s == null) return "—";
  if (s % 3600 === 0) return `каждые ${s / 3600} ч`;
  if (s % 60 === 0) return `каждые ${s / 60} мин`;
  return `каждые ${s} с`;
}
