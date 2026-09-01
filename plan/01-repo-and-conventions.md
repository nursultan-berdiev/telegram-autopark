# 01 — Монорепо, стек, конвенции

## Целевая структура репозитория

Реструктурируем текущий `telegram-autopark/` (сейчас — один пакет `bot/`) в монорепо:

```
telegram-autopark/
  services/
    core-api/
      app/
        __init__.py
        main.py                 # FastAPI app, роутеры, lifespan (engine, scheduler)
        config.py               # pydantic-settings (см. ниже)
        db/
          base.py               # async engine + async_session_maker (перенос из bot/db/base.py)
          models.py             # ВСЕ SQLAlchemy-модели (перенос + новые, см. 02)
          session.py            # FastAPI-dependency get_session()
        domain/                 # чистая бизнес-логика (перенос bot/services/*, без Telegram)
          cars.py drivers.py invitations.py schedules.py payments.py
          reports.py reminders.py fines.py maintenance.py telemetry.py rules.py
        clients/
          adapter.py            # httpx-клиент к tracker-adapter
          ai_gateway.py         # перенос http-backend из bot/services/ai.py
        routers/                # HTTP-эндпоинты (тонкие, вызывают domain/*)
          cars.py drivers.py invitations.py schedules.py payments.py reports.py
          telemetry.py fines.py maintenance.py rules.py alerts.py commands.py auth.py
        rules/
          engine.py             # оценщик правил (APScheduler job)
          evaluators.py         # per-type: overdue_payment, fines_count, maintenance_km
        storage.py              # файловое хранилище чеков/селфи (перенос из bot/services/storage.py, без Bot)
      alembic/                  # миграции (перенос из корня; владелец — core-api)
      alembic.ini
      tests/                    # перенос доменных тестов + новые
      Dockerfile
      requirements.txt
    tracker-adapter/
      app/
        __init__.py
        main.py                 # FastAPI app + ingest-воркер (lifespan)
        config.py
        providers/
          base.py               # интерфейс TrackerProvider (ABC/Protocol)
          traccar.py            # TraccarProvider (REST + WebSocket)
          registry.py           # provider by name
        ingest.py               # воркер: WS Traccar → нормализация → POST core-api /telemetry/batch
        commands.py             # приём команд от core-api → провайдер
        clients/core_api.py     # httpx-клиент к core-api (ingest push)
      tests/
      Dockerfile
      requirements.txt
    bot/                        # существующий пакет, рефакторится в тонкий клиент (см. 05)
      app/                      # ЕДИНЫЙ стиль с core-api/adapter: код под app/
        __main__.py __init__.py config.py callbacks.py filters.py logger.py scheduler.py
        handlers/ keyboards/ states/ middlewares/
        client/apiclient.py     # httpx-клиент к core-api (новый)
      tests/
      Dockerfile
      requirements.txt
  libs/
    contracts/                  # общие Pydantic-DTO HTTP-контрактов (импортируются всеми)
      __init__.py
      telemetry.py cars.py commands.py alerts.py common.py
  docker-compose.yml            # postgres + core-api + tracker-adapter + bot
  .env.example
  README.md
```

> Примечание про переезд: домен (`bot/db/models.py`, `bot/services/*` кроме Telegram-специфики)
> **физически перемещается** в `services/core-api/app/{db,domain}`. Импорты `from bot.services...`
> в боте заменяются на вызовы `ApiClient`. `bot/services/storage.py` и загрузка Telegram-файлов
> остаются в боте; распознавание чека (claude-gateway) переезжает в core-api.

## `libs/contracts`

Общие DTO, чтобы контракты HTTP были типизированы у клиента и сервера. Пример:
`TelemetryPoint`, `CarStateDTO` (в т.ч. вычисленный `online` и `last_point_age_seconds` — в БД
не хранятся), `CommandRequest`/`CommandResult`, `AlertDTO`, `CarDTO`, `DriverDTO`,
`ScheduleStatusDTO`, `PageParams`. Пакет с `pyproject.toml` (устанавливаемый).

**Docker (важно — иначе не соберётся):** контекст сборки — **корень репо**, не папка сервиса,
т.к. `../../libs` из контекста сервиса не виден. В compose:
```yaml
core-api:
  build: { context: ., dockerfile: services/core-api/Dockerfile }
```
В Dockerfile сначала ставим contracts: `COPY libs/contracts /libs/contracts` →
`pip install /libs/contracts`, затем код сервиса. Аналогично для adapter и bot.

## Стек (сохраняем существующий, добавляем FastAPI)

- Python 3.12, async везде.
- **core-api / tracker-adapter**: FastAPI + uvicorn, Pydantic v2 / pydantic-settings, httpx,
  SQLAlchemy 2.0 (async, только в core-api), asyncpg, Alembic (только core-api), APScheduler.
- **bot**: aiogram 3.15 (без изменений версии), httpx (клиент core-api), APScheduler (джобы:
  reminders-триггер и опрос алертов — теперь через API).
- Тесты: pytest + pytest-asyncio + aiosqlite (in-memory), + httpx ASGITransport для API-тестов.
**Конфиг тестов при переезде:** корневой `pytest.ini` (`testpaths = tests`, `asyncio_mode=auto`)
не подхватится из `services/core-api`. Завести **per-service** `pytest.ini`/`pyproject`
(сохранить `asyncio_mode=auto`) и `conftest.py` рядом с тестами каждого сервиса. Прогон —
`cd services/<svc> && python -m pytest`.

Точные версии — из текущего `requirements.txt` (aiogram==3.15.0, SQLAlchemy[asyncio]==2.0.36,
asyncpg==0.30.0, alembic==1.14.0, pydantic-settings==2.7.0, anthropic==0.42.0, httpx==0.28.1,
APScheduler==3.11.0). Добавить: `fastapi`, `uvicorn[standard]`, `websockets` (для Traccar WS).

## Конфигурация (env) — по сервисам

Наследуем паттерн `bot/config.py` (pydantic-settings, `extra="ignore"`, singleton `settings`).

**core-api** (`services/core-api/app/config.py`):
- `DATABASE_URL` (`postgresql+asyncpg://…`), `CORE_API_TOKEN` (bearer для бота/UI),
  `INGEST_TOKEN` (bearer **только** для `POST /telemetry/batch`; отдельный от `CORE_API_TOKEN`,
  чтобы компрометация адаптера не давала прав на команды), `TZ` (default `Asia/Bishkek`),
  `FILES_DIR`, `INVITE_TTL_HOURS`/`INVITE_TTL_MINUTES` (**TTL инвайта живёт здесь** — инвайты
  создаёт и валидирует core-api; из бота убрать).
- ИИ (переезд из бота): `AI_BACKEND` {http,api,cli,auto}, `GATEWAY_URL`, `GATEWAY_API_KEY`,
  `GATEWAY_MODEL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `AI_TIMEOUT_SECONDS`.
- Адаптер: `ADAPTER_URL`, `ADAPTER_TOKEN`.
- Правила/алерты: `RULES_ENABLED` (0/1), `RULES_INTERVAL_SECONDS` (напр. 120), `REMINDER_HOUR`.
- Тайминги (именованные, НЕ хардкодить «N»):
  - `TELEMETRY_STALE_SECONDS` (default 300) — порог «нет свежей телеметрии → offline» (вычисление
    `online`, safety gate). Учесть SLEEP MODE на части машин.
  - `COMMAND_ACK_WINDOW_SECONDS` (default 180) — окно ожидания подтверждения блокировки битом 27
    (`sent`→`acked`, иначе `unconfirmed`).
  - `COMMAND_DEDUP_SECONDS` (default 60) — окно идемпотентности команд по `(alert_id, type)`.
  - `TELEMETRY_RETENTION_DAYS` (default 180) — чистка телеметрии.
- `ADMIN_IDS` (для авторизации ролей по tg_id — тот же валидатор `"111,222"`→`list[int]`).

**tracker-adapter** (`services/tracker-adapter/app/config.py`):
- `CORE_API_URL`, `INGEST_TOKEN` (пуш телеметрии в core-api — не общий `CORE_API_TOKEN`),
  `ADAPTER_TOKEN` (проверка входящих команд от core-api).
- `TRACCAR_URL` (напр. `http://traccar:8082` или `http://127.0.0.1:8082`), `TRACCAR_USER`,
  `TRACCAR_PASSWORD` (или token), `TRACCAR_WS_ENABLED` (1), `TELEMETRY_BATCH_SIZE`,
  `TELEMETRY_FLUSH_SECONDS`.

**bot** (`services/bot/app/config.py` — правка текущего `bot/config.py`):
- Удаляем `DATABASE_URL`, ИИ-переменные и `INVITE_TTL_*` (уезжают в core-api).
- Добавляем `CORE_API_URL`, `CORE_API_TOKEN`.
- Оставляем `BOT_TOKEN`, `ADMIN_IDS`, `TZ`, `REMINDERS_ENABLED`, `REMINDER_HOUR`.

## Git-конвенции (для агентов)

- Одна подзадача = одна ветка от `feature/overdue-partial-payments` (или от согласованной базы)
  = один коммит. Ревьюер — AlterEgoNurs.
- Комментарии в коде — только «почему», ≤ 2 строк.
- Не трогать чужие Alembic-ревизии; новые — по одной на подзадачу, линейной цепочкой (см. 02).
- Не ломать тесты: `python -m pytest` в `services/core-api` зелёный после каждого шага.

## Docker-compose (итог)

Сервисы: `postgres` (postgres:16-alpine, healthcheck, том `pg_data`), `core-api`, `tracker-adapter`,
`bot`. У каждого — `build: {context: ., dockerfile: services/<svc>/Dockerfile}` (контекст = корень
репо, чтобы видеть `libs/`, см. выше). `core-api` зависит от postgres,
`alembic upgrade head && uvicorn app.main:app`; `tracker-adapter` — во внешней сети до Traccar
(по образцу текущего `claude_net`); `bot` зависит от core-api. Тома: `pg_data`, `files`
(чеки/селфи, монтируется в core-api).
