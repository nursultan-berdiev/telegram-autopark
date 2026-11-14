import { useId, useState, type ReactNode } from "react";

// Сворачиваемая строка: содержимое монтируется только при открытии — его
// запросы к API тоже уходят только тогда.
export default function Collapsible({
  title,
  children,
  defaultOpen = false,
}: {
  title: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();
  return (
    <section className="collapsible">
      <button type="button" className="coll-head" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen((o) => !o)}>
        <span>{title}</span>
        <span className="coll-chevron" aria-hidden>
          {open ? "▴" : "▾"}
        </span>
      </button>
      {open && (
        <div className="coll-body" id={bodyId}>
          {children}
        </div>
      )}
    </section>
  );
}
