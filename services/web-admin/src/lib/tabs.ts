// Вкладка из ?tab=: только из белого списка, иначе — первая по умолчанию.
export function pickTab<T extends string>(raw: string | null, keys: readonly T[], fallback: T): T {
  return keys.find((k) => k === raw) ?? fallback;
}

// Тот же адрес с другой вкладкой: остальные параметры запроса не теряются.
export function withTab(params: URLSearchParams, tab: string): URLSearchParams {
  const next = new URLSearchParams(params);
  next.set("tab", tab);
  return next;
}
