// Подписи карточки машины: что показать и каким цветом. Чистые функции, без
// React — правила проверяются тестами, а карточка только раскладывает строки.
import type { CarOverview } from "./types";

export type Tone = "bad" | "warn" | "ok" | "muted";
export interface Line {
  text: string;
  tone: Tone;
}

const BLOCK_REASON: Record<string, string> = {
  manual: "вручную",
  overdue: "за неоплату",
  fines: "за штрафы",
  photo: "за фотоконтроль",
};

export function money(value: string | number | null): string {
  if (value == null) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return `${n.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} сом`;
}

// «14.11» в текущем году, иначе «14.11.2025».
export function shortDate(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString("ru-RU", sameYear ? { day: "2-digit", month: "2-digit" } : undefined);
}

// Сколько дней до даты YYYY-MM-DD по календарю (0 — сегодня, отрицательное — прошла).
export function daysUntil(day: string, now: Date = new Date()): number {
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((Date.parse(`${day}T00:00:00Z`) - today) / 86_400_000);
}

export function lastPayment(c: CarOverview, now?: Date): Line {
  if (!c.last_payment_at) return { text: "оплат не было", tone: "muted" };
  return { text: `${shortDate(c.last_payment_at, now)} · ${money(c.last_payment_amount)}`, tone: "ok" };
}

export function paymentDue(c: CarOverview, now?: Date): Line | null {
  if (c.payment_overdue_days > 0) {
    return { text: `просрочка ${c.payment_overdue_days} дн. · долг ${money(c.payment_debt)}`, tone: "bad" };
  }
  if (c.next_payment_due) return { text: `следующая до ${shortDate(c.next_payment_due, now)}`, tone: "muted" };
  if (c.driver_id) return { text: "графика оплат нет", tone: "muted" };
  return null;
}

export function isBlocked(c: CarOverview): boolean {
  return c.engine_blocked || c.block_status === "acked";
}

export function engine(c: CarOverview): Line {
  const reason = c.block_source ? BLOCK_REASON[c.block_source] : undefined;
  const why = reason ? ` ${reason}` : "";
  if (isBlocked(c)) return { text: `заблокирован${why}`, tone: "bad" };
  if (c.block_status === "armed") return { text: `блокировка${why} ждёт остановки`, tone: "warn" };
  if (c.block_status) return { text: `блокировка${why} отправлена`, tone: "warn" };
  return { text: "не заблокирован", tone: "ok" };
}

export function lastInspection(c: CarOverview, now?: Date): Line {
  if (!c.last_inspection_at) return { text: "ещё не было", tone: "muted" };
  return { text: `был ${shortDate(c.last_inspection_at, now)}`, tone: "muted" };
}

export function inspectionDue(c: CarOverview, now?: Date): Line {
  if (!c.driver_id || !c.inspection_due_on) return { text: "не нужен: нет водителя", tone: "muted" };
  if (c.inspection_overdue_days > 0) return { text: `просрочен на ${c.inspection_overdue_days} дн.`, tone: "bad" };
  const left = daysUntil(c.inspection_due_on, now);
  if (left <= 0) return { text: "нужно сделать сегодня", tone: "warn" };
  if (left === 1) return { text: "нужно сделать завтра", tone: "warn" };
  return { text: `следующий до ${shortDate(c.inspection_due_on, now)}`, tone: "ok" };
}
