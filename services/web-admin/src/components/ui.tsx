import type { ReactNode } from "react";

export function Loading() {
  return <div className="muted">Загрузка…</div>;
}

export function ErrorBox({ error }: { error: string }) {
  return <div className="error">Ошибка: {error}</div>;
}

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="card">
      {title && <h2>{title}</h2>}
      {children}
    </section>
  );
}

// Обёртка списка: единообразно показывает загрузку/ошибку/пусто.
export function Async<T>({
  loading,
  error,
  data,
  empty,
  children,
}: {
  loading: boolean;
  error: string | null;
  data: T | null;
  empty?: string;
  children: (data: T) => ReactNode;
}) {
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (data == null || (Array.isArray(data) && data.length === 0))
    return <div className="muted">{empty ?? "Пусто"}</div>;
  return <>{children(data)}</>;
}

export function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}
