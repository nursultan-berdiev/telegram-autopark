import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useApi } from "../lib/hooks";
import type { Car, Driver } from "../lib/types";

const MIN_QUERY = 2; // минимум символов, чтобы начать поиск
const PER_TYPE = 4; // сколько показывать машин и сколько водителей (квота на тип)

type Hit = { kind: "car" | "driver"; id: number; title: string; sub: string };

// Быстрый переход: по гос. номеру/модели → карточка машины, по имени → карточка
// водителя. Данные (машины + активные водители) грузим лениво, при первом фокусе.
export default function SearchBox() {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const cars = useApi<Car[]>(loaded ? "/cars" : null);
  const drivers = useApi<Driver[]>(loaded ? "/drivers?active=true" : null);
  const boxRef = useRef<HTMLDivElement>(null);

  const query = q.trim().toLowerCase();
  const results = useMemo<Hit[]>(() => {
    if (query.length < MIN_QUERY) return [];
    const carHits: Hit[] = (cars.data ?? [])
      .filter((c) => c.plate.toLowerCase().includes(query) || (c.model ?? "").toLowerCase().includes(query))
      .slice(0, PER_TYPE)
      .map((c) => ({ kind: "car", id: c.id, title: c.plate, sub: c.model ?? "" }));
    const driverHits: Hit[] = (drivers.data ?? [])
      .filter((d) => d.full_name.toLowerCase().includes(query))
      .slice(0, PER_TYPE)
      .map((d) => ({ kind: "driver", id: d.id, title: d.full_name, sub: d.car_plate ?? "" }));
    return [...carHits, ...driverHits];
  }, [query, cars.data, drivers.data]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const go = (r: Hit) => {
    nav(r.kind === "car" ? `/cars/${r.id}` : `/drivers/${r.id}`);
    setQ("");
    setOpen(false);
  };

  // Пустой выпадающий список различает загрузку, ошибку и «ничего не нашлось»:
  // при сбое /cars или /drivers нельзя писать «Ничего не найдено».
  const loading = cars.loading || drivers.loading;
  const failed = Boolean(cars.error || drivers.error);
  const emptyText = loading ? "Поиск…" : failed ? "Не удалось загрузить данные" : "Ничего не найдено";

  return (
    <div className="searchbox" ref={boxRef}>
      <label className="search">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="11" cy="11" r="7" />
          <path d="M21 21l-4.3-4.3" />
        </svg>
        <input
          placeholder="Поиск по номеру или водителю"
          value={q}
          onFocus={() => {
            setLoaded(true);
            setOpen(true);
          }}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && results.length) {
              e.preventDefault();
              go(results[0]);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
        />
      </label>
      {open && query.length >= MIN_QUERY && (
        <div className="search-results">
          {results.length === 0 ? (
            <div className="search-empty">{emptyText}</div>
          ) : (
            results.map((r) => (
              <button type="button" key={`${r.kind}-${r.id}`} className="search-hit" onClick={() => go(r)}>
                <span className="i">{r.kind === "car" ? "▦" : "◍"}</span>
                <span className="search-hit-main">
                  <span className="search-hit-title">{r.title}</span>
                  {r.sub && <span className="search-hit-sub">{r.sub}</span>}
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
