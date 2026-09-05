# 03 — core-api (FastAPI): домен по HTTP

Владелец БД и бизнес-логики. Тонкие роутеры → `app/domain/*`. Домен переезжает из
`bot/services/*` (функции уже `session`-first — оборачиваются легко). Единственный писатель БД.

## Скелет приложения

- `app/main.py`: `FastAPI()`, подключение роутеров, `lifespan` (создать engine из
  `app/db/base.py`; запустить APScheduler для правил и фоновой чистки телеметрии; корректно
  гасить). Мидлварь аутентификации: проверяет `Authorization: Bearer <token>` и определяет
  **область** токена — `CORE_API_TOKEN` → домен и команды; `INGEST_TOKEN` → только
  `POST /telemetry/batch`; чужой скоуп → 403 (см. раздел «Аутентификация и роли»).
- `app/db/session.py`: dependency `async def get_session() -> AsyncSession` (`async with
  async_session_maker() as s: yield s`) — заменяет «сессия через middleware» из бота.
- Ошибки домена → HTTP: not found → 404, конфликт (напр. занятый номер) → 409, валидация → 422.

## Аутентификация и роли

**Три раздельных сервисных токена (не один общий — иначе компрометация адаптера = блокировка
двигателей):**
- `CORE_API_TOKEN` — бот → core-api (доменные и командные эндпоинты).
- `INGEST_TOKEN` — только для `POST /telemetry/batch` (им владеет tracker-adapter). Область —
  один эндпоинт; на команды/домен прав не даёт.
- `ADAPTER_TOKEN` — core-api → tracker-adapter (входящие на адаптер).

**Роль пользователя** (admin/driver/guest) — по `tg_user_id` из заголовка `X-TG-User-Id`.
`GET /me?tg_id=` возвращает роль/водителя. Логика — перенос из `bot/middlewares/role.py`: admin
если `tg_id in ADMIN_IDS`; иначе активный driver; иначе guest.

**Авторизация чувствительных действий на сервере (не доверять клиенту):**
- `POST /cars/{id}/commands` (блокировка/разблокировка/arm/disarm) — core-api **сам проверяет**
  `requested_by in ADMIN_IDS`; неадмин → 403. `requested_by` из тела не считать доказательством
  прав без этой проверки.
- Команды доступны только по `CORE_API_TOKEN` (не по `INGEST_TOKEN`).

## Переезд домена (1:1 из bot/services)

Перенести в `app/domain/` без изменения сигнатур (первый арг `session: AsyncSession`):
`cars.py`, `drivers.py` (+`DriverStats`), `invitations.py`, `schedules.py` (вся логика
долга/просрочки — `schedule_status`, `apply_payment`, `advance_due_date`, …),
`payments.py` (`receipt_hash`, `is_duplicate`, `create_payment`), `reports.py`
(`build_snapshot`, `upcoming_payments`, …), `reminders.py` (`collect`, `mark_reminded`).
ИИ-распознавание (`bot/services/ai.py`) → `app/clients/ai_gateway.py` (оставить http/api/cli
бэкенды; вызывать из роутера `/payments/recognize`). Файлы (`storage.py`) → `app/storage.py`
(без `Bot`; принимает байты).

## HTTP-эндпоинты (v1)

Формы запросов/ответов — Pydantic-DTO из `libs/contracts`. Ниже — контур.

### Домен (для бота/UI)
```
GET  /me?tg_id=                         -> {role, driver?}
# Машины
GET  /cars                              -> [CarDTO]            (?free=1 — только свободные)
POST /cars                              (plate, model?, photo…) -> CarDTO   (409 если plate занят)
GET  /cars/{id}                         -> CarDTO (+ driver, + car_state кратко)
DELETE /cars/{id}                       -> 204 (409 если occupied)
# Водители
GET  /drivers?active=1                  -> [DriverDTO]
GET  /drivers/{id}                      -> DriverDTO (+stats)
POST /drivers/register                  (invite flow) -> DriverDTO
POST /drivers/{id}/fire                 -> {freed_plate}
# Приглашения
POST /invitations                       (car_id) -> {code, expires_at}
GET  /invitations/resolve?code=         -> InviteCheck {ok, problem?}
# Графики
GET  /drivers/{id}/schedule             -> ScheduleDTO + ScheduleStatusDTO
PUT  /drivers/{id}/schedule             (period, interval_days?, amount, next_due_date) -> ...
# Платежи
POST /payments/recognize                (bytes чека + media_type) -> RecognizedReceipt
POST /payments                          (driver_id, amount, paid_at, receipt…, recognized) -> PaymentDTO
GET  /drivers/{id}/payments             -> [PaymentDTO]
# Отчёты и ИИ-ассистент
GET  /reports/cars-drivers | /reports/upcoming | /reports/by-driver | /reports/by-car
POST /assistant/query                   (question) -> {answer}   # build_snapshot + gateway
# Напоминания (бот тянет по своему cron и рассылает; core-api только считает)
GET  /reminders/plan?now=               -> {reminders[], owner_lines[], total_debt}  # reminders.collect
POST /reminders/mark                     (schedule_ids[], date) -> 204                 # reminders.mark_reminded
```

### Телеметрия и состояние (ингест от адаптера + чтение для клиентов)
```
POST /telemetry/batch                   [TelemetryPoint...]  (Bearer INGEST_TOKEN)
  -> пишет в telemetry, обновляет car_state, обновляет одометр для ТО, дергает движок правил
GET  /cars/{id}/state                   -> CarStateDTO         ("где машина сейчас")
  # CarStateDTO отдаёт online ВЫЧИСЛЕННЫМ (now-last_ts < TELEMETRY_STALE_SECONDS) — в БД не хранится,
  # + last_point_age_seconds (админу видеть «данных нет 40 мин»)
GET  /cars/{id}/telemetry?from=&to=&limit=  -> [TelemetryPoint]   (история/трек)
```
Маппинг `TelemetryPoint.external_id` → `trackers.external_id` → `car_id`. Неизвестный
`external_id` — лог + 202 (не 500), чтобы адаптер не зависал.

### Трекеры (привязка машина↔устройство)
```
GET  /cars/{id}/tracker                 -> TrackerDTO|null
PUT  /cars/{id}/tracker                 (provider, external_id, config?) -> TrackerDTO
  # СМЕНА ТРЕКЕРА: одометр = пробег трекера, не авто (R8). При привязке нового external_id
  # переустановить у maintenance_items last_service_km (от текущего пробега) и
  # last_service_tracker_id (S1); выставить car_state.odometer_trusted=true; предупредить админа.
DELETE /cars/{id}/tracker               -> 204
```

### Штрафы и ТО
```
GET/POST/DELETE /cars/{id}/fines        -> [FineDTO]/FineDTO/204
POST /fines/{fine_id}/pay                -> FineDTO   (status→paid, paid_at:=now; снимает алерт fines_count)
GET/PUT /cars/{id}/maintenance          -> [MaintenanceDTO]/MaintenanceDTO
POST /cars/{id}/maintenance/{type}/done -> ТО выполнено: last_service_km := текущий одометр,
     last_service_tracker_id := car_state.odometer_tracker_id, car_state.odometer_trusted := true
     (снимает системный алерт odometer_untrusted, если был)
```

### Правила, алерты, команды (см. 06)
```
GET/POST/PUT/DELETE /rules
GET  /alerts?status=open[&car_id=]      -> [AlertDTO]     (бот опрашивает)
POST /alerts/{id}/ack | /resolve
POST /cars/{id}/commands                (type=engine_block|engine_unblock|alarm_arm|alarm_disarm,
                                         requested_by, alert_id?) -> CommandResult
  # engine_block → safety gate → adapter; пишет commands; см. 06
GET  /cars/{id}/commands                -> [CommandDTO]   (аудит)
```

## Фоновые задачи (APScheduler в lifespan)

- **rules.engine** — раз в `RULES_INTERVAL_SECONDS` (напр. 120с): оценка правил по всем машинам,
  создание/резолв алертов (см. 06). Также разово дергается из `/telemetry/batch` для
  maintenance_km, чтобы реагировать быстро на пробег.
- **reminders** — перенос ежедневного cron (09:00 TZ) `collect` → но теперь доставка через бота:
  либо core-api складывает «напоминания» и бот их опрашивает, либо бот сам вызывает
  `GET /reminders/plan` по расписанию и рассылает. Решение: **бот тянет план по своему cron**
  (`GET /reminders/plan?now=`), core-api только считает (переиспользует `reminders.collect`),
  антиспам `mark_reminded` — через `POST /reminders/mark`.
- **command timeout** — часто (напр. раз в 30–60с): команды в статусе `sent` старше
  `COMMAND_ACK_WINDOW_SECONDS` без подтверждения битом 27 → `unconfirmed` **+ завести системный
  алерт `command_unconfirmed`** (rule_id=NULL), который бот доставит опросом `/alerts` (S4 —
  у core-api нет Telegram-пуша). Обязательна: при оффлайне трекера телеметрии нет, и обработчик
  `/telemetry/batch` статус не переведёт (R1).
- **telemetry cleanup** — раз в сутки удалять телеметрию старше `TELEMETRY_RETENTION_DAYS`.

## Клиенты (исходящие HTTP)

- `app/clients/adapter.py` — httpx к tracker-adapter: `send_command(external_id, command)`,
  `get_state(external_id)` (fallback, обычно состояние берём из БД). Bearer `ADAPTER_TOKEN`.
- `app/clients/ai_gateway.py` — перенос http-бэкенда (claude-gateway `/v1/extract`,`/v1/prompt`).

## Что переиспользовать (точки в текущем коде)

- `bot/services/schedules.py` — весь расчёт долга/просрочки → правило overdue_payment и
  ScheduleStatusDTO. НЕ переписывать логику, только перенести.
- `bot/services/reminders.py`, `reports.py`, `invitations.py`, `payments.py`, `ai.py`
  (http-бэкенд), `storage.py` — перенос.
- `bot/config.py` — паттерн pydantic-settings и валидатор `ADMIN_IDS`.
- Конвенции тестов из `tests/conftest.py` — для доменных тестов (см. 08).
