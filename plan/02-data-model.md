# 02 — Модель данных

БД: PostgreSQL, SQLAlchemy 2.0 async (`asyncpg`), Alembic. Деньги — `Numeric(12,2)` (Decimal),
время — `DateTime(timezone=True)` (UTC), пробег — километры `Numeric(12,3)`. Все модели в
`services/core-api/app/db/models.py`. Владелец схемы и миграций — **только core-api**.

## Существующие таблицы (переезжают как есть, НЕ ломать)

Текущая цепочка миграций: `0001_initial → 0002_schedule_paid → 0003_schedule_remind →
0004_driver_fired → 0005_receipt_kind` (head). Таблицы:

- **cars**: `id PK`, `plate str(32) UNIQUE` (бизнес-ключ), `model str|None`, `photo_file_id`,
  `photo_path`, `status CarStatus{free,occupied}=free`, `created_at`.
- **drivers**: `id PK`, `tg_user_id BigInt UNIQUE idx`, `full_name`, `phone`, `inn`,
  `selfie_file_id/path`, `car_id FK→cars.id ON DELETE SET NULL (nullable)`, `active bool=true`,
  `fired_at|None`, `created_at`.
- **invitations**: `id`, `code str(64) UNIQUE idx`, `car_id FK→cars.id CASCADE`,
  `created_by BigInt`, `created_at`, `expires_at`, `status InviteStatus{active,used,expired}`,
  `used_by BigInt|None`.
- **payment_schedules**: `id`, `driver_id FK→drivers.id CASCADE UNIQUE`,
  `period SchedulePeriod{daily,weekly,monthly,custom}`, `interval_days int|None`,
  `amount Numeric(12,2)`, `paid_in_period Numeric(12,2)=0`, `next_due_date`, `active bool=true`,
  `last_reminded_on Date|None`, `created_at`.
- **payments**: `id`, `driver_id FK CASCADE`, `car_id FK SET NULL|None`, `amount Numeric(12,2)`,
  `paid_at|None`, `receipt_file_id/path`, `receipt_kind str(16)="photo"`, `receipt_hash str(64)
  idx|None`, `recognized_data Text|None (JSON)`, `status PaymentStatus{confirmed}`, `created_at`.

`test_app.py` сейчас пиннит ровно эти 5 таблиц — **этот тест обновить** при добавлении новых
(см. 08).

## Новые таблицы

### Миграция `0006_tracking` — трекеры, телеметрия, состояние

**trackers** — связка машина↔устройство + точка расширяемости (req 1):
```
id            int PK
car_id        int FK→cars.id ON DELETE CASCADE, idx
provider      Enum TrackerProvider{traccar}   # расширяется новыми значениями
external_id   str(64)   # Traccar uniqueId, напр. "9175358042"
config        JSONB|None    # напр. {"protocol":"h02","port":5013,"command_password":"0000"}
active        bool = true
created_at    DateTime(tz)
UNIQUE(provider, external_id)   # одно устройство не привязать дважды
```
> Одна машина может иметь ≥1 трекера в будущем; для v1 достаточно 1 активного. Правила/команды
> адресуют машину; адаптер резолвит активный трекер машины.

**telemetry** — временной ряд метрик (req 3), пишет только core-api из ингеста адаптера:
```
id                bigint PK
car_id            int FK→cars.id CASCADE, idx
tracker_id        int FK→trackers.id SET NULL|None
ts                DateTime(tz)         # deviceTime (время устройства)
server_ts         DateTime(tz)         # когда получено
lat               float|None
lon               float|None
speed_knots       float|None
course            float|None
altitude          float|None
valid             bool                 # A/V из кадра
ignition          bool|None            # бит 10 статус-маски
motion            bool|None
total_distance_km Numeric(12,3)|None   # одометр (Traccar totalDistance, в км)
engine_blocked    bool|None            # бит 27 статус-маски
status_raw        str(16)|None         # hex статус-маски, напр. "fffffbff"
attributes        JSONB|None           # прочие поля (io1..io4, protocol, raw)
INDEX (car_id, ts)                     # выборки истории/последнего
```
> Объём (интервал 30с ⇒ ~2 880 точек/машину/сутки): ~15 тыс. строк/сутки на нынешние 5 машин,
> ~86 тыс. на 30 машин. Для PostgreSQL — пустяк; партиционирование не нужно, хватит обычной
> фоновой чистки (напр. хранить 180 дней). TimescaleDB — отложенная опция при кратном росте.

**car_state** — последний снимок (быстрый «сейчас», обновляется на каждом апдейте телеметрии):
```
car_id          int PK FK→cars.id CASCADE
tracker_id      int|None
last_ts         DateTime(tz)|None
lat, lon        float|None
speed_knots     float|None
ignition        bool|None
motion          bool|None
odometer_km     Numeric(12,3)|None   # пробег ТРЕКЕРА (Traccar totalDistance), НЕ авто — см. ниже
odometer_tracker_id int|None         # от какого трекера этот пробег (ловить смену трекера)
odometer_trusted bool = true         # false, если поймали немонотонный скачок (сброс счётчика)
engine_blocked  bool = false         # подтверждается телеметрией (бит 27)
last_command    str(32)|None         # последняя команда (для UX)
updated_at      DateTime(tz)
```
> **online НЕ хранить булевой колонкой** — она не протухнет (машина ушла в оффлайн → телеметрии
> нет → выставить false некому → safety gate увидит «online» по устаревшему `last_ts` и отправит
> команду в никуда). `online` **вычислять при чтении** из `last_ts`: `online = now − last_ts <
> TELEMETRY_STALE_SECONDS` (default 300, конфиг в 01). Порог — из факта «интервал 30 с, сменить
> нельзя (S71 игнорируется)», с запасом; учесть `SLEEP MODE` (у 139 OFF, на других может быть ON
> → кадры реже). В safety gate «нет свежей телеметрии» = небезопасно (не блокировать).

### Миграция `0007_fines_maintenance` — штрафы и ТО (данные для правил v1)

**fines** — штрафы, ручной ввод админом v1:
```
id           int PK
car_id       int FK→cars.id CASCADE, idx
driver_id    int FK→drivers.id SET NULL|None   # кто был за рулём (если известно)
amount       Numeric(12,2)|None
currency     str(8)|None            # как в чеках — свободная строка, не конвертируем
issued_at    DateTime(tz)           # дата нарушения/штрафа
status       Enum FineStatus{unpaid, paid} = unpaid   # чтобы правило fines_count могло resolve
paid_at      DateTime(tz)|None
source       str(32) = "manual"     # manual|import(будущее)
external_ref str(64)|None           # номер постановления и т.п.
note         Text|None
created_by   BigInt                 # tg_id админа
created_at   DateTime(tz)
INDEX (car_id, issued_at)
```
> Без статуса правило `fines_count` (≥ N) не переставало бы выполняться никогда → open-алерт
> висит вечно, авто-resolve невозможен. Эвалуатор считает **только `unpaid`**; окно `window_days`
> опционально. «Оплатил штраф» = админ переводит в `paid` (`paid_at:=now`).

**maintenance_items** — обслуживание по пробегу:
```
id                int PK
car_id            int FK→cars.id CASCADE, idx
type              str(32)            # "oil" | "filter" | ...  (v1: oil)
interval_km       Numeric(12,3)      # напр. 10000.000
last_service_km   Numeric(12,3)      # пробег трекера на момент последнего ТО (база)
last_service_tracker_id int|None FK→trackers.id SET NULL   # от какого трекера взята база (R8/S1)
last_service_at   DateTime(tz)|None
note              Text|None
created_by        BigInt
created_at, updated_at DateTime(tz)
```
> Правило `maintenance_km` считает `car_state.odometer_km − last_service_km ≥ interval_km`.
> «Выполнил ТО» = админ обновляет `last_service_km := текущий одометр`, `last_service_at := now`.
>
> ⚠️ **Одометр — это пробег ТРЕКЕРА (Traccar totalDistance), а не автомобиля.** При замене
> трекера, пересоздании устройства в Traccar или переносе на другой инстанс счётчик
> обнуляется/скачет → `odometer − last_service_km` даст мусор (ложные ТО или пропущенные). Поэтому:
> `maintenance_items` привязывать к трекеру (`last_service_tracker_id`); при
> `PUT /cars/{id}/tracker` (смена трекера) **обязательно переустанавливать `last_service_km` от
> нового текущего значения** (+ `last_service_tracker_id`) и предупреждать админа; в `car_state`
> держать `odometer_tracker_id`. Это «пробег по трекеру», не «одометр авто».
>
> **S2 — детект сброса счётчика (тот же uniqueId, но totalDistance обнулился: пересоздали
> устройство/перенесли инстанс — `odometer_tracker_id` совпадёт и не спасёт).** В обработчике
> `/telemetry/batch`: если новый `total_distance_km < car_state.odometer_km` (с запасом на шум,
> напр. > 1 км вниз) → выставить `car_state.odometer_trusted=false`. При `odometer_trusted=false`
> правило `maintenance_km` **не считать**; вернуть доверие (`true`) может только явная
> переустановка базы (`maintenance/{type}/done` или `PUT /cars/{id}/tracker`).

### Миграция `0008_rules_alerts_commands` — движок правил

**rules** — определения правил (расширяемо; см. 06):
```
id         int PK
car_id     int FK→cars.id CASCADE, nullable   # NULL = применяется ко всем машинам
type       Enum RuleType{overdue_payment, fines_count, maintenance_km}
params     JSONB          # напр. {"min_debt": 0} | {"count": 3, "window_days": 90} | {"grace_km": 0}
enabled    bool = true
severity   str(16) = "warning"    # info|warning|critical (для UX)
created_at, updated_at
```

**alerts** — срабатывания правил:
```
id            int PK
rule_id       int FK→rules.id CASCADE, **nullable**   # NULL для системных алертов (не от правила)
car_id        int FK→cars.id CASCADE, idx
type          Enum AlertType   # rule-типы + системные (см. ниже)
severity      str(16)          # info|warning|critical
status        Enum AlertStatus{open, acknowledged, resolved} = open, idx
triggered_at  DateTime(tz)   # момент ПЕРВОГО срабатывания — при конфликте НЕ обновлять (U1)
last_seen_at  DateTime(tz)   # обновляется на каждом повторном срабатывании (свежесть снимка)
resolved_at   DateTime(tz)|None
payload       JSONB          # снимок: долг/кол-во штрафов/пробег, человекочитаемый текст
action_taken  str(32)|None   # напр. "engine_block" | "ignored"
```
> **AlertType** = rule-типы `{overdue_payment, fines_count, maintenance_km}` **плюс системные**
> `{command_unconfirmed (S4), odometer_untrusted (S3)}`. Системные алерты рождаются не движком
> правил, а джобами/обработчиками (rule_id=NULL) и доставляются тем же путём (бот опрашивает
> `GET /alerts?status=open`) — единый канал вместо отсутствующего у core-api Telegram-пуша.
>
> **severity (T5):** rule-алерт **наследует `severity` своего правила** (`rules.severity`);
> системные задают свою фиксированно — `command_unconfirmed = warning`, `odometer_untrusted = info`.
> **Дедуп открытых алертов — на уровне БД, не в domain-логике.** Правила/джобы дергаются из
> нескольких точек (таймер движка + `/telemetry/batch` + command-timeout) → гонка даёт два open.
> В миграции 0008 — **два частичных уникальных индекса PostgreSQL** (rule-алерты и системные):
> `CREATE UNIQUE INDEX uq_alert_open_rule ON alerts (rule_id, car_id) WHERE status='open' AND rule_id IS NOT NULL;`
> `CREATE UNIQUE INDEX uq_alert_open_sys  ON alerts (car_id, type)   WHERE status='open' AND rule_id IS NULL;`
> Вставку делать `INSERT ... ON CONFLICT DO UPDATE SET payload=EXCLUDED.payload, last_seen_at=now()`
> (не `DO NOTHING` — иначе новый снимок по уже открытому алерту потеряется). **`triggered_at` при
> конфликте НЕ трогать** (U1) — иначе он вечно «только что» (эвалуатор ходит каждые
> RULES_INTERVAL_SECONDS + из /telemetry/batch), и «висит с 20.08» превратится в «минуту назад».

**commands** — аудит исходящих команд на трекер (req 5, безопасность):
```
id           int PK
car_id       int FK→cars.id CASCADE, idx
tracker_id   int|None
type         Enum CommandType{engine_stop, engine_resume, alarm_arm, alarm_disarm}
status       Enum CommandStatus{queued, blocked_by_safety, sent, acked, unconfirmed, failed}
             # queued→(гейт)→blocked_by_safety | sent→(телеметрия бит27 в окно)→acked, иначе unconfirmed | failed
acked_at     DateTime(tz)|None
requested_by BigInt|None    # tg_id инициатора (или null для системы)
alert_id     int FK→alerts.id SET NULL|None   # если из алерта
safety_snapshot JSONB|None  # состояние car_state на момент проверки гейта
result       Text|None       # ответ Traccar / причина отказа
created_at, updated_at
```

## Enum'ы (добавить в models.py)

`TrackerProvider{traccar}`, `RuleType{overdue_payment, fines_count, maintenance_km}`,
`AlertType{overdue_payment, fines_count, maintenance_km, command_unconfirmed, odometer_untrusted}`,
`AlertStatus{open, acknowledged, resolved}`, `FineStatus{unpaid, paid}`,
`CommandType{engine_stop, engine_resume, alarm_arm, alarm_disarm}`,
`CommandStatus{queued, blocked_by_safety, sent, acked, unconfirmed, failed}`.
Существующие (`CarStatus, InviteStatus, SchedulePeriod, PaymentStatus`) — без изменений.

## Правила по миграциям (для агентов)

- Пишем руками, линейной цепочкой: `0005 → 0006 → 0007 → 0008`. `down_revision` строго на
  предыдущую. Не редактировать 0001–0005.
- `alembic/env.py` уже async и берёт URL из настроек; импортирует модели для `Base.metadata`.
  После переезда — поправить импорт на `app.db.models`.
- Автоген не настроен — ревизии пишем явно (как существующие). После — прогнать
  `alembic upgrade head` на чистой БД и на копии текущей.
- `test_app.py` пиннит список таблиц/роутеров — обновлять вместе с миграцией.
