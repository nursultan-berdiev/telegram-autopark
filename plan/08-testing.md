# 08 — Тестирование и верификация

Существующий набор — сильный (сервис-уровневые тесты на async SQLite in-memory, без сети).
Сохраняем стиль. Не ломать зелёный прогон на каждом шаге.

## Конвенции (наследуем из текущего tests/)

- `conftest.py`: env выставляется **до** импорта конфигурации сервиса
  (`BOT_TOKEN`/`ADMIN_IDS`/`CORE_API_URL`/`CORE_API_TOKEN` в боте; для core-api —
  `DATABASE_URL`=sqlite dummy, `CORE_API_TOKEN`, `INGEST_TOKEN`, `ADMIN_IDS`).
- Фикстура `session` (pytest_asyncio): свежий `create_async_engine("sqlite+aiosqlite:///
  :memory:")` на тест, `Base.metadata.create_all`, `async_sessionmaker(expire_on_commit=False)`.
  Фикстура `session_maker` — сам maker (для подмены в API-зависимости `get_session`).
- `asyncio_mode=auto` — **per-service** `pytest.ini`/`pyproject` (корневой `pytest.ini` с
  `testpaths=tests` из `services/<svc>` не подхватится). У каждого сервиса — свой `conftest.py`
  рядом с тестами. Прогон: `cd services/<svc> && python -m pytest`.
- Фабрики создания данных в каждом файле (`_hire`, `_schedule`, `_driver_with_schedule`,
  `_seed`, `_make_car`) — переиспользовать/вынести общий модуль фабрик при переезде.
- Время — явный `datetime(..., tzinfo=timezone.utc)`; SQLite naive → сравнивать через
  `.replace(tzinfo=None)`. Деньги — `float(Decimal)` в ассертах.

## По слоям

### Домен (core-api/tests) — основной массив, как сейчас
- Переехавшие тесты (cars/drivers/payments/schedules/reminders/reports/invitations/registration)
  — зелёные без изменений логики.
- Ключевые инварианты сохранить (эталон — эти тесты): расчёт `advance_due_date` (monthly-clamp
  на конец месяца), частичная оплата держит срок, переплата закрывает несколько периодов,
  формула долга `остаток + (просроч.периодов−1)×сумма`, «срок сегодня» ≠ «просрочка», дедуп
  чека по хэшу, увольнение = архив (история сохраняется), лениво-истекающие инвайты, `car_taken`.

### Новое доменное (core-api/tests)
- **Эвалуаторы правил**: overdue_payment (границы min_days/min_debt; переиспользует
  schedule_status), fines_count (ровно N−1/N/N+1; окно window_days), maintenance_km (границы
  grace_km; одометр из car_state).
- **Алерты**: не плодятся дубли (второй проход не создаёт второй open); авто-resolve при снятии
  условия; ack/resolve переходы статусов.
- **Системные алерты (T4)**: два `command_unconfirmed` по одной машине → **один** open +
  `payload` обновлён на свежую команду (`ON CONFLICT DO UPDATE`, индекс `uq_alert_open_sys`);
  `odometer_untrusted` авто-resolve после переустановки базы (`maintenance/{type}/done` или
  `PUT /cars/{id}/tracker`); `odometer_trusted=false` реально **блокирует** расчёт
  `maintenance_km`; `odometer_untrusted` НЕ поднимается, если у машины нет `maintenance_items` (T3);
  severity: rule-алерт = severity правила, `command_unconfirmed=warning`, `odometer_untrusted=info` (T5).
- **Защитный гейт блокировки (критично)**: отказ при `speed_knots>=1`; при `motion=true`; при
  `ignition on`; при несвежей телеметрии (устаревший `last_ts` → online=false); успех только при
  `speed<1 ∧ motion=false ∧ ignition=false ∧ online`; корректный `commands.status`
  (`blocked_by_safety` vs `sent`) и `safety_snapshot`. Плюс: неадмин `requested_by`→403;
  идемпотентность по `(alert_id,type)`; `sent→acked` по биту 27 в окно, иначе `unconfirmed`.
- **Телеметрия**: `POST /telemetry/batch` пишет строки, обновляет `car_state` (в т.ч.
  `engine_blocked` из бита 27), обновляет одометр; **`online` НЕ пишется** — вычисляется при
  чтении из `last_ts` (тест: `GET /cars/{id}/state` отдаёт `online=false` и «возраст точки», если
  `last_ts` старше `TELEMETRY_STALE_SECONDS`, при этом строка телеметрии на месте); неизвестный
  `external_id` → 202, не 500.

### Контрактные (core-api/tests) — HTTP
- `httpx.AsyncClient(transport=ASGITransport(app))`; подменить `get_session` на in-memory maker.
- Проверять коды/формы: 200/201/204/404/409/422; auth (401 без Bearer); роль по `X-TG-User-Id`.
- **Разделение токенов (строго, без развилок):** `INGEST_TOKEN` на `POST /cars/{id}/commands`
  → **403**; `CORE_API_TOKEN` на `POST /telemetry/batch` → **403** (ингест принимает ТОЛЬКО
  `INGEST_TOKEN` — решение S6); `POST /cars/{id}/commands` с `requested_by` не из ADMIN_IDS → 403.

### Адаптер (tracker-adapter/tests)
- `TraccarProvider` на **моках** REST (`/api/session`, `/api/devices`, `/api/commands/send`) и
  WS-потока: корректный маппинг NormalizedPoint (ignition/motion/totalDistance→км/бит27→
  engine_blocked/status_raw), маппинг команд (engine_stop→engineStop и т.д.), резолв
  uniqueId→deviceId, реконнект/бэкофф ингеста, батч-флаш в core-api (мок).
- Command-API: гейта нет (это core-api); проверяем прозрачную передачу и формат ответа.

### Бот (bot/tests)
- Dispatch-тесты на **моке `ApiClient`** (не сервисов): хендлеры дергают верные методы API и
  рендерят ответы/кнопки; FakeSession ловит `SendMessage/SendPhoto/SendDocument`; приём чека
  (photo/PDF/screenshot-as-file), отклонение video/txt/oversize — до вызова API.
- Callback блокировки: `BlockCB` pack/unpack ≤64 байт; хендлер вызывает `api.command(...)` и
  показывает итог (заблокировано / отказ гейта / ошибка).
- **Карточка по типу алерта (T2):** rule-типы → есть кнопка «Заблокировать»; `command_unconfirmed`
  → кнопки «Заблокировать» НЕТ, есть [Повторить]/[Понятно]; `odometer_untrusted` → «Заблокировать»
  НЕТ, есть [ТО выполнено].

## test_app-пиннинг
Текущий `test_app.py` фиксирует 5 таблиц и 10 роутеров бота. При переезде:
- В core-api завести аналог, пиннящий полный набор таблиц (растёт с миграциями 0006–0008).
- В боте — пиннинг набора роутеров/наличия `ApiClient`, без БД-таблиц.
Обновлять пиннинг вместе с каждой миграцией/новым роутером — это «страховка от случайных
изменений схемы».

## E2E / интеграция (ручная + скрипты)
Рядовой E2E — **без железа**, на OSMAnd-потоке; всё, что требует живого трекера с реле, вынесено
в веху **5-HW** (см. 07).
- `docker compose up` (postgres+core-api+adapter+bot). Проверить `/health` адаптера и core-api.
- **Ингест (OSMAnd):** Traccar Client на телефоне → телеметрия падает в БД и `car_state`
  (позиция/online; зажигание/пробег/бит27 у телефона не будет). Требует открытого 5055 (см. 07/09).
- Симуляция правила: создать `overdue_payment`-правило и просроченный график → дождаться алерта
  → в боте карточка с кнопкой; безопасность гейта проверяется на эмулированной телеметрии (ниже).
- **[5-HW, только с авто и SIM]** блокировка на **стоящей** машине с подтверждённым реле:
  «Заблокировать» → `engineStop` доходит до Traccar (`S20,OK`) → `car_state.engine_blocked` по
  биту 27 → «Разблокировать» → `engineResume`. Требует номер SIM + переключение авто на наш
  Traccar (`8040000 <ip> 5013`) — сейчас недоступно (см. 07 «Железо»).
- **Безопасность (без железа):** эмулировать телеметрию `speed_knots>=1` / `motion=true` /
  `ignition on` → попытка блокировки → `blocked_by_safety`, команда на трекер НЕ ушла.

## Гейты качества (CI, желательно)
- `pytest` во всех сервисах; `alembic upgrade head` на чистой БД; линт; `docker compose config`.
