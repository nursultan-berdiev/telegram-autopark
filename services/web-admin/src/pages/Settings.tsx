import { useSearchParams } from "react-router-dom";
import Periodic from "./Periodic";
import Documents from "./Documents";

// То, что не относится к конкретной машине: автоматизации по всему парку и
// общие документы. Вкладка — из URL (?tab=), как в журналах: старые адреса
// /periodic и /documents перенаправляют сюда на нужную вкладку.
type Tab = "automations" | "documents";
const TABS: { key: Tab; label: string }[] = [
  { key: "automations", label: "Автоматизации" },
  { key: "documents", label: "Общие документы" },
];

export default function Settings() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab");
  const tab: Tab = TABS.some((t) => t.key === requested) ? (requested as Tab) : "automations";

  return (
    <>
      <h1>Настройки</h1>
      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={tab === t.key ? "active" : ""}
            onClick={() => setParams({ tab: t.key }, { replace: true })}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "automations" ? <Periodic /> : <Documents />}
    </>
  );
}
