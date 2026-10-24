// Типы ответов core-api — только поля, которые рисуем в SPA-скелете.

export interface WhoAmI {
  tg_user_id: number;
  is_admin: boolean;
}

export interface Car {
  id: number;
  plate: string;
  model: string | null;
  status: string;
  driver_id: number | null;
  driver_name: string | null;
  created_at: string | null;
}

export interface Driver {
  id: number;
  tg_user_id: number;
  full_name: string;
  phone: string | null;
  inn: string | null;
  car_id: number | null;
  car_plate: string | null;
  active: boolean;
  created_at: string | null;
}

export interface DriverStats {
  payments_count: number;
  total_paid: string;
}

export interface DriverWithStats {
  driver: Driver;
  stats: DriverStats;
}

export interface Schedule {
  id: number;
  period: string;
  interval_days: number | null;
  amount: string;
  paid_in_period: string;
  next_due_date: string | null;
  active: boolean;
}

export interface ScheduleStatus {
  summary: string;
  period_label: string;
  debt_now: string;
  is_overdue: boolean;
  next_due_date: string | null;
}

export interface ScheduleWithStatus {
  schedule: Schedule | null;
  status: ScheduleStatus | null;
}

// Соответствует CarStateDTO (libs/contracts/contracts/cars.py).
export interface CarState {
  car_id: number;
  tracker_id: number | null;
  last_ts: string | null;
  lat: number | null;
  lon: number | null;
  speed_knots: number | null;
  ignition: boolean | null;
  motion: boolean | null;
  engine_blocked: boolean;
  last_command: string | null;
  online: boolean;
}

export interface Command {
  id: number;
  type: string;
  status: string;
  requested_by: number | null;
  source: string | null;
  result: string | null;
  created_at: string | null;
}

// Соответствует FineDTO (libs/contracts/contracts/fleet.py).
export interface CommandResult {
  command: Command;
  ok: boolean;
  reason: string | null;
}

export interface Fine {
  id: number;
  amount: string | null;
  amount_to_pay: string | null;
  issued_at: string | null;
  status: string;
  paid_at: string | null;
}

// Соответствует CarDriverRow (libs/contracts/contracts/reports.py).
export interface CarDriverRow {
  plate: string;
  model: string | null;
  status: string;
  driver_name: string | null;
  driver_phone: string | null;
}

export interface UpcomingRow {
  driver_id: number;
  driver_name: string;
  plate: string | null;
  next_due_date: string | null;
  amount: string;
  debt_now: string;
  is_overdue: boolean;
  overdue_days: number;
  summary: string;
}

export interface Alert {
  id: number;
  car_id: number;
  type: string;
  severity: string;
  status: string;
  triggered_at: string | null;
  payload: Record<string, unknown> | null;
}

export interface AuditRow {
  id: number;
  actor_tg_user_id: number | null;
  source: string;
  method: string;
  path: string;
  status_code: number;
  params: Record<string, unknown> | null;
  created_at: string | null;
}

export interface OutboundRow {
  id: number;
  recipient_tg_user_id: number;
  kind: string;
  text: string;
  attempts: number;
  created_at: string | null;
  sent_at: string | null;
  failed_at: string | null;
  cancelled_at: string | null;
}

export interface TaskRun {
  id: number;
  task: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  detail: string | null;
  requested_by: number | null;
}
