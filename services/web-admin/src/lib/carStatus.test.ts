import { describe, expect, it } from "vitest";
import { engine, inspectionDue, lastPayment, money, paymentDue } from "./carStatus";
import type { CarOverview } from "./types";

const NOW = new Date(2026, 10, 14, 12, 0); // 14.11.2026, местное время

function car(over: Partial<CarOverview> = {}): CarOverview {
  return {
    id: 1, plate: "01KG195API", model: "Honda Stream", status: "occupied", has_photo: false,
    driver_id: 5, driver_name: "Иванов", last_payment_at: null, last_payment_amount: null,
    next_payment_due: null, payment_overdue_days: 0, payment_debt: "0", online: true,
    engine_blocked: false, block_status: null, block_source: null, last_inspection_at: null,
    inspection_due_on: "2026-11-20", inspection_overdue_days: 0, inspection_days_left: 6,
    unpaid_fines: 0, open_alerts: 0,
    ...over,
  };
}

describe("карточка машины", () => {
  it("деньги — в сомах с пробелами тысяч", () => {
    expect(money("1500.00").replace(/\s/g, " ")).toBe("1 500 сом");
    expect(money(null)).toBe("—");
  });

  it("оплата: последняя и просрочка с долгом", () => {
    expect(lastPayment(car()).text).toBe("оплат не было");
    const paid = car({ last_payment_at: "2026-11-12T08:00:00Z", last_payment_amount: "1800.00" });
    expect(lastPayment(paid, NOW).text.replace(/\s/g, " ")).toBe("12.11 · 1 800 сом");
    const late = paymentDue(car({ payment_overdue_days: 3, payment_debt: "5400" }), NOW)!;
    expect(late.tone).toBe("bad");
    expect(late.text.replace(/\s/g, " ")).toBe("просрочка 3 дн. · долг 5 400 сом");
    expect(paymentDue(car({ driver_id: null }))).toBeNull(); // свободной машине график не нужен
  });

  it("двигатель: факт по трекеру, ожидание и отправка блокировки", () => {
    expect(engine(car())).toEqual({ text: "не заблокирован", tone: "ok" });
    expect(engine(car({ engine_blocked: true, block_status: "acked", block_source: "overdue" }))).toEqual({
      text: "заблокирован за неоплату",
      tone: "bad",
    });
    expect(engine(car({ block_status: "armed", block_source: "fines" })).text).toBe("блокировка за штрафы ждёт остановки");
    expect(engine(car({ block_status: "sent", block_source: "manual" })).tone).toBe("warn");
  });

  it("фотоконтроль: нет водителя, просрочка, сегодня, завтра, позже", () => {
    const none = car({ driver_id: null, inspection_due_on: null, inspection_days_left: null });
    expect(inspectionDue(none).text).toBe("не нужен: нет водителя");
    expect(inspectionDue(car({ inspection_overdue_days: 4, inspection_days_left: -4 })).tone).toBe("bad");
    const today = car({ inspection_due_on: "2026-11-14", inspection_days_left: 0 });
    expect(inspectionDue(today, NOW).text).toBe("нужно сделать сегодня");
    const tomorrow = car({ inspection_due_on: "2026-11-15", inspection_days_left: 1 });
    expect(inspectionDue(tomorrow, NOW).text).toBe("нужно сделать завтра");
    expect(inspectionDue(car(), NOW).text).toBe("следующий до 20.11");
  });
});
