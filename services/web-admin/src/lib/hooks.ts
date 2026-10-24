import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

// Загрузка одного GET-ресурса. path=null — не грузить (условные запросы).
export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (path == null) {
      setLoading(false);
      return;
    }
    let live = true;
    setLoading(true);
    setError(null);
    api
      .get<T>(path)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e?.message ?? String(e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [path, tick]);

  // reload — перечитать после действия (мутации) или по таймеру/фокусу.
  // Стабильная ссылка (useCallback), чтобы её можно было класть в deps эффектов.
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}
