# Платформа управления автопарком

Аренда автомобилей: водители по приглашению, индивидуальные графики платежей, приём чеков
с ИИ-распознаванием, GPS-телеметрия, правила и дистанционная блокировка двигателя.

План рефакторинга и все проектные решения — в [plan/](plan/) (11 брифов).

## Архитектура

```
                 ┌──────────────┐        ┌────────────────────────────┐
   Telegram ───► │ bot (aiogram)│──HTTP─►│                            │
                 └──────────────┘        │          core-api          │──► PostgreSQL
   (будущий UI) ───────HTTP─────────────►│  FastAPI: домен и правила  │
                                         │  единственный писатель БД  │
                 ┌────────────────────┐  │                            │
   Traccar ◄WS/REST│ tracker-adapter │◄─│  команды на блокировку      │
                   │ провайдеры трекеров│─►│  POST /telemetry/batch   │
                   └────────────────────┘  └────────────────────────────┘
```

| Сервис | Что делает |
|---|---|
| [services/core-api](services/core-api) | Владелец БД и бизнес-логики. REST для всех клиентов, движок правил, алерты, команды с защитным гейтом. |
| [services/tracker-adapter](services/tracker-adapter) | Слой трекинг-платформ. Провайдер `traccar` (SinoTrack ST-901M, протокол H02): ингест телеметрии по WebSocket и команды по REST. |
| [services/bot](services/bot) | Telegram-клиент: диалоги, файлы, рендер, кнопки. В БД не ходит. |
| [libs/contracts](libs/contracts) | Общие Pydantic-DTO HTTP-контрактов. |

## Ключевые решения

- **Блокировка двигателя — только вручную по алерту.** Автономной immobilization нет.
  Перед отправкой команды — защитный гейт: машина стоит (`speed < 1` узла и `motion=false`),
  зажигание выключено, телеметрия свежая. Отказ гейта — штатный статус `blocked_by_safety`.
- **Подтверждение блокировки** приходит битом 27 статус-маски, а не ответом трекера.
  Если за `COMMAND_ACK_WINDOW_SECONDS` подтверждения нет, фоновая джоба переводит команду
  в `unconfirmed` и поднимает алерт админу («возможно, нет реле»).
- **`online` не хранится в БД** — считается от `last_ts` при чтении, иначе офлайн-машина
  осталась бы «на связи» навсегда.
- **Одометр принадлежит трекеру, а не машине.** Смена устройства или сброс счётчика метит
  пробег недостоверным, и правило ТО не считается, пока админ не переустановит базу.
- **Три токена с разными областями:** `CORE_API_TOKEN` (бот → домен и команды),
  `INGEST_TOKEN` (адаптер → только `/telemetry/batch`), `ADAPTER_TOKEN` (core-api → адаптер).

## Запуск (docker)

```bash
cp .env.example .env   # заполнить BOT_TOKEN, ADMIN_IDS, токены, TRACCAR_*
docker compose up -d --build
```

Контекст сборки у всех сервисов — корень репозитория: им нужен общий пакет `libs/contracts`.
ИИ живёт во внешнем `claude-gateway` (сеть `claude_net`), ключей в образах нет.

## Разработка

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv -r services/core-api/requirements.txt
uv pip install --python .venv -r services/bot/requirements.txt
uv pip install --python .venv -r services/tracker-adapter/requirements.txt
uv pip install --python .venv -e libs/contracts
```

Тесты — по сервисам (у каждого свой `pytest.ini`):

```bash
cd services/core-api      && ../../.venv/bin/python -m pytest
cd services/bot           && ../../.venv/bin/python -m pytest
cd services/tracker-adapter && ../../.venv/bin/python -m pytest
```

Миграции (владелец схемы — только core-api):

```bash
cd services/core-api && DATABASE_URL=... ../../.venv/bin/python -m alembic upgrade head
```

## Документация

- [plan/](plan/) — брифы рефакторинга: модель данных, API, адаптер, движок правил, фазы, тесты
- [plan/09-traccar-reference.md](plan/09-traccar-reference.md) — факты Traccar/H02 (команды, биты статуса)
- [ИНСТРУКЦИЯ_АДМИНА.md](ИНСТРУКЦИЯ_АДМИНА.md), [QA_ИНСТРУКЦИЯ.md](QA_ИНСТРУКЦИЯ.md) — эталон поведения бота
- [Функциональные_требования.md](Функциональные_требования.md)
