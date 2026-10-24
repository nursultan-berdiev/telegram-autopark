import { useState, type ReactNode } from "react";

// Кнопка действия: подтверждение (опц.), индикатор, инлайн-ошибка, колбэк после.
export function ActionButton({
  label,
  confirm,
  danger,
  onAction,
  onDone,
}: {
  label: string;
  confirm?: string;
  danger?: boolean;
  onAction: () => Promise<unknown>;
  onDone?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const run = async () => {
    if (confirm && !window.confirm(confirm)) return;
    setBusy(true);
    setErr(null);
    setInfo(null);
    try {
      // Строковый результат действия — информационное сообщение (напр. «взведено»).
      const result = await onAction();
      if (typeof result === "string") setInfo(result);
      onDone?.();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="action">
      <button className={danger ? "danger" : ""} disabled={busy} onClick={run}>
        {busy ? "…" : label}
      </button>
      {err && <span className="error"> {err}</span>}
      {info && <span className="info"> {info}</span>}
    </span>
  );
}

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

// Обёртка списка/карточки. «Загрузка…» показываем только при ПЕРВОЙ загрузке
// (data==null): при reload после действия данные остаются на месте (без мигания),
// а ошибка перечитывания показывается над контентом, не затирая кнопки.
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
  if (data == null) {
    if (loading) return <Loading />;
    if (error) return <ErrorBox error={error} />;
    return <div className="muted">{empty ?? "Пусто"}</div>;
  }
  const isEmpty = Array.isArray(data) && data.length === 0;
  return (
    <>
      {error && <ErrorBox error={error} />}
      {isEmpty ? <div className="muted">{empty ?? "Пусто"}</div> : children(data)}
    </>
  );
}

export function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}
