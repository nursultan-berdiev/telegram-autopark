import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { api } from "../lib/api";
import { ActionButton, Async, Card, fmtDate } from "../components/ui";
import { ToneLine } from "../components/CarPhoto";
import Collapsible from "../components/Collapsible";
import DocumentList from "../components/DocumentList";
import Automations from "../components/car/Automations";
import EngineCommands from "../components/car/EngineCommands";
import FinesTab from "../components/car/FinesTab";
import Gallery from "../components/car/Gallery";
import InspectionPanel from "../components/car/InspectionPanel";
import MaintenanceCard from "../components/car/MaintenanceCard";
import PaymentsTab from "../components/car/PaymentsTab";
import TrackerCard from "../components/car/TrackerCard";
import { engine, lastPayment, paymentDue, type Line } from "../lib/carStatus";
import type { CarOverview, CarState, Command, CommandResult, Maintenance, PhotoInspection } from "../lib/types";

function trackerLine(s: CarState | null): Line {
  if (!s) return { text: "—", tone: "muted" };
  const link = s.online ? "на связи" : "нет связи";
  return { text: s.last_ts ? `${link} · точка ${fmtDate(s.last_ts)}` : link, tone: s.online ? "ok" : "muted" };
}

function finesLine(c: CarOverview): Line {
  return c.unpaid_fines > 0
    ? { text: `неоплаченных: ${c.unpaid_fines}`, tone: "bad" }
    : { text: "неоплаченных нет", tone: "ok" };
}

function serviceLine(items: Maintenance[] | null): Line {
  if (!items) return { text: "—", tone: "muted" };
  if (!items.length) return { text: "не настроено", tone: "muted" };
  const late = items.filter((m) => m.over_km != null && Number(m.over_km) > 0);
  if (late.length) return { text: `просрочено: ${late.map((m) => m.type).join(", ")}`, tone: "bad" };
  return { text: "в норме", tone: "ok" };
}

export default function CarDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  // Шапка и факты — та же карточка, что на главной: подписи совпадают один в один.
  const overview = useApi<CarOverview[]>("/cars/overview");
  const state = useApi<CarState>(`/cars/${id}/state`);
  const commands = useApi<Command[]>(`/cars/${id}/commands`);
  const inspections = useApi<PhotoInspection[]>(`/cars/${id}/photo-inspections?limit=200`);
  const maintenance = useApi<Maintenance[]>(`/cars/${id}/maintenance`);
  const [picked, setPicked] = useState<number | null>(null);
  const car = overview.data?.find((c) => String(c.id) === id) ?? null;
  const list = inspections.data ?? [];
  const shown = list.find((i) => i.id === picked) ?? list[0] ?? null;

  const refreshEngine = () => {
    overview.reload();
    state.reload();
    commands.reload();
  };

  // Возвращает строку-инфо (показывается у кнопки) или бросает ошибку.
  const sendCommand = async (type: "engine_block" | "engine_unblock") => {
    const res = await api.post<CommandResult>(`/cars/${id}/commands`, {
      type,
      arm_if_unsafe: type === "engine_block",
    });
    if (res.ok) return "Команда отправлена";
    const st = res.command.status;
    if (st === "armed") return "Взведено — сработает на безопасной остановке";
    if (st === "sent" || st === "acked" || st === "unconfirmed") return "Команда уже в работе";
    // Отказ гейта (blocked_by_safety) и т.п. — реальная неудача.
    throw new Error(res.reason ?? "команда не выполнена");
  };

  return (
    <>
      <p className="back">
        <Link to="/">← К машинам</Link>
      </p>
      <Async loading={overview.loading} error={overview.error} data={car} empty="Машина не найдена">
        {(c) => (
          <>
            <div className="car-page-head">
              <h1>{c.plate}</h1>
              <span className="car-model">{c.model ?? "марка не указана"}</span>
              {c.open_alerts > 0 && (
                <Link className="chip warn" to="/journals?tab=alerts">
                  Алерты: {c.open_alerts}
                </Link>
              )}
              <div className="actions-row">
                <ActionButton
                  label="Заблокировать двигатель"
                  confirm="Заблокировать двигатель? На едущей машине блокировка взведётся и сработает на ближайшей безопасной остановке."
                  danger
                  onAction={() => sendCommand("engine_block")}
                  onDone={refreshEngine}
                />
                <ActionButton
                  label="Разблокировать"
                  confirm="Разблокировать двигатель?"
                  onAction={() => sendCommand("engine_unblock")}
                  onDone={refreshEngine}
                />
                {c.status === "free" && (
                  <ActionButton
                    label="Удалить машину"
                    confirm="Удалить машину? Доступно только для свободной машины без истории."
                    danger
                    onAction={() => api.del(`/cars/${id}`)}
                    onDone={() => navigate("/")}
                  />
                )}
              </div>
            </div>

            <div className="car-layout">
              <div className="car-col">
                <Card>
                  <Gallery car={c} inspection={shown} jump={picked != null} />
                </Card>
                <InspectionPanel car={c} list={list} selectedId={shown?.id ?? null} onSelect={setPicked} />
              </div>
              <div className="car-col">
                <Card title="Коротко о машине">
                  <dl className="facts-list">
                    <dt>Водитель</dt>
                    <dd>
                      {c.driver_id ? (
                        <Link to={`/drivers/${c.driver_id}`}>{c.driver_name ?? "водитель без имени"}</Link>
                      ) : (
                        <span className="tone-muted">свободна</span>
                      )}
                    </dd>
                    <dt>Оплата</dt>
                    <dd>
                      <ToneLine line={lastPayment(c)} />
                      <ToneLine line={paymentDue(c)} sub />
                    </dd>
                    <dt>Двигатель</dt>
                    <dd>
                      <ToneLine line={engine(c)} />
                    </dd>
                    <dt>Трекер</dt>
                    <dd>
                      <ToneLine line={trackerLine(state.data)} />
                    </dd>
                    <dt>Штрафы</dt>
                    <dd>
                      <ToneLine line={finesLine(c)} />
                    </dd>
                    <dt>ТО</dt>
                    <dd>
                      <ToneLine line={serviceLine(maintenance.data)} />
                    </dd>
                  </dl>
                </Card>
                <Card title="Документы машины">
                  <DocumentList path={`/cars/${id}/documents`} />
                  <p className="muted hint">Загрузка — через бота. Общие документы парка — в «Настройках».</p>
                </Card>
              </div>
            </div>

            <div className="collapsibles">
              <Collapsible title={c.unpaid_fines > 0 ? `Штрафы · неоплаченных ${c.unpaid_fines}` : "Штрафы"}>
                <FinesTab carId={id!} />
              </Collapsible>
              <Collapsible title="Оплаты">
                <PaymentsTab carId={id!} />
              </Collapsible>
              <Collapsible title="ТО и автоматизации">
                <MaintenanceCard carId={id!} />
                <Automations car={c} />
              </Collapsible>
              <Collapsible title="Трекер и команды двигателя">
                <Card title="Где машина">
                  <Async loading={state.loading} error={state.error} data={state.data}>
                    {(s) => (
                      <dl className="kv">
                        <dt>На связи</dt><dd>{s.online ? "да" : "нет"}</dd>
                        <dt>Зажигание</dt><dd>{s.ignition == null ? "—" : s.ignition ? "вкл" : "выкл"}</dd>
                        <dt>Координаты</dt>
                        <dd>{s.lat != null && s.lon != null ? `${s.lat.toFixed(5)}, ${s.lon.toFixed(5)}` : "—"}</dd>
                        <dt>Последняя точка</dt><dd>{fmtDate(s.last_ts)}</dd>
                      </dl>
                    )}
                  </Async>
                </Card>
                <TrackerCard carId={id!} />
                <EngineCommands carId={id!} commands={commands} onChanged={refreshEngine} />
              </Collapsible>
            </div>
          </>
        )}
      </Async>
    </>
  );
}
