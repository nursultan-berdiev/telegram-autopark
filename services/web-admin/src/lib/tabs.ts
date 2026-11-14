// Вкладка из ?tab=: только из белого списка, иначе — первая по умолчанию.
export function pickTab<T extends string>(raw: string | null, keys: readonly T[], fallback: T): T {
  return keys.find((k) => k === raw) ?? fallback;
}
