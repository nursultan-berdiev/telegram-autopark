import { useSearchParams } from "react-router-dom";
import Periodic from "./Periodic";
import Documents from "./Documents";
import { pickTab, withTab } from "../lib/tabs";

// То, что не относится к конкретной машине: автоматизации по всему парку и
// общие документы. Вкладка — из URL (?tab=), как в журналах: старые адреса
// /periodic и /documents перенаправляют сюда на нужную вкладку.
const TABS = [
  { key: "automations", label: "Автоматизации" },
  { key: "documents", label: "Общие документы" },
] as const;
const KEYS = TABS.map((t) => t.key);

export default function Settings() {
  const [params, setParams] = useSearchParams();
  const tab = pickTab(params.get("tab"), KEYS, "automations");

  return (
    <>
      <h1>Настройки</h1>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={`settings-tab-${t.key}`}
            aria-controls="settings-tabpanel"
            aria-selected={tab === t.key}
            className={tab === t.key ? "active" : ""}
            onClick={() => setParams(withTab(params, t.key), { replace: true })}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id="settings-tabpanel" aria-labelledby={`settings-tab-${tab}`}>
        {tab === "automations" ? <Periodic /> : <Documents />}
      </div>
    </>
  );
}
