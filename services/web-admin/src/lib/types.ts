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

// Соответствует TrackerDTO (libs/contracts/contracts/cars.py).
export interface Tracker {
  id: number;
  car_id: number;
  provider: string;
  external_id: string;
  config: Record<string, unknown> | null;
  active: boolean;
}

// Соответствует MaintenanceDTO (libs/contracts/contracts/fleet.py). Decimal → строка.
export interface Maintenance {
  id: number;
  car_id: number;
  type: string;
  interval_km: string;
  last_service_km: string;
  last_service_tracker_id: number | null;
  last_service_at: string | null;
  note: string | null;
  over_km: string | null;
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

export interface Invitation {
  code: string;
  car_id: number;
  expires_at: string | null;
  ttl_label: string;
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

// Соответствует AnnouncementDTO (libs/contracts/contracts/announcements.py).
export interface Announcement {
  id: number;
  text: string;
  created_by: number;
  recipients: number;
  created_at: string | null;
}

// Соответствует PeriodicTaskDTO (libs/contracts/contracts/fleet.py).
export interface PeriodicTask {
  id: number;
  name: string;
  task: string;
  interval_seconds: number | null;
  crontab: string | null;
  args: Record<string, unknown> | null;
  enabled: boolean;
  last_run_at: string | null;
  total_run_count: number;
  created_at: string | null;
}

// Соответствует DocumentDTO (libs/contracts/contracts/documents.py).
// file_kind: "photo" (фото из Telegram = jpeg) | "document" (файл: может быть
// картинкой или PDF). car_id=null — общий документ парка.
export interface Document {
  id: number;
  car_id: number | null;
  title: string;
  file_kind: string;
  file_name: string | null;
  file_size: number | null;
  created_by: number;
  created_at: string | null;
}

// Соответствует PhotoStatusDTO (libs/contracts/contracts/photo.py).
// next_due_on — локальная дата парка (YYYY-MM-DD); overdue_days 0 — не просрочен.
export interface PhotoStatus {
  driver_id: number;
  driver_name: string;
  car_id: number;
  car_plate: string;
  next_due_on: string;
  overdue_days: number;
  last_inspection_id: number | null;
  last_inspection_at: string | null;
}

// Соответствует PhotoInspectionDTO. Боков (left/right) может не быть у старых
// осмотров — тогда null (показывать нечего).
export interface PhotoInspection {
  id: number;
  driver_id: number;
  driver_name: string | null;
  car_id: number | null;
  car_plate: string | null;
  front_file_id: string;
  left_file_id: string | null;
  rear_file_id: string;
  right_file_id: string | null;
  interior_front_file_id: string;
  interior_rear_file_id: string;
  has_issues: boolean;
  comment: string | null;
  created_at: string | null;
}

// Соответствует AssistantAnswer (libs/contracts/contracts/reports.py).
export interface AssistantAnswer {
  answer: string;
}
