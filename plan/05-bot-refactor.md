# 05 — Бот: полный рефактор в тонкий HTTP-клиент

Решение заказчика: **полный рефактор**. Бот перестаёт ходить в БД; вся доменная логика — в
core-api. Бот отвечает только за Telegram: диалоги (FSM), файлы, рендер, кнопки, доставку
напоминаний и алертов.

## Что удаляем/уносим из бота

- `bot/db/` (models, base) — **уносим в core-api**. В боте БД нет.
- `bot/services/*` доменные — уносим в core-api (`app/domain`). В боте остаётся только
  Telegram-специфика.
- `RoleMiddleware` больше **не открывает сессию БД**. Роль берём из core-api `GET /me?tg_id=`
  (можно кэшировать на короткий TTL). Мидлварь теперь кладёт в `data`: `role`, `driver`
  (DTO из API), и `api` (экземпляр `ApiClient`).
- Из `config.py` убрать `DATABASE_URL` и ИИ-переменные; добавить `CORE_API_URL`,
  `CORE_API_TOKEN`.

## Что остаётся в боте

- `handlers/`, `keyboards/`, `states/`, `callbacks.py`, `filters.py`, `scheduler.py`, `logger.py`,
  `__main__.py` — но хендлеры вызывают `ApiClient`, а не сервисы.
- Загрузка Telegram-файлов (`bot download` → байты) — остаётся в боте; байты чека/селфи
  отправляются в core-api.
- Рассылка сообщений/кнопок, FSM-диалоги.

## ApiClient (`services/bot/app/client/apiclient.py`)

httpx-обёртка над core-api. **Образец — существующий http-бэкенд** `bot/services/ai.py`
(`httpx.AsyncClient`, `Authorization: Bearer`, `settings.gateway_url`). Методы 1:1 к эндпоинтам
core-api из [03-core-api.md](03-core-api.md), напр.:
```
me(tg_id) ; cars(free=False) ; create_car(...) ; car(id) ; delete_car(id)
drivers(active) ; driver(id) ; register_driver(...) ; fire_driver(id)
create_invitation(car_id) ; resolve_invitation(code)
get_schedule(driver_id) ; set_schedule(driver_id, ...)
recognize_receipt(bytes, media_type) ; create_payment(...) ; payments(driver_id)
reports_* ; assistant_query(question)
reminders_plan(now) ; reminders_mark(schedule_ids, date)
car_state(id) ; car_telemetry(id, from, to) ; set_tracker(id, provider, external_id)
fines_* ; maintenance_* ; alerts(status) ; ack_alert(id) ; command(car_id, type, ...)
```
Общий Bearer-заголовок `CORE_API_TOKEN` + прокидывать `X-TG-User-Id` для ролевых проверок.
Ошибки API → аккуратные сообщения пользователю (404/409/422 → человеческий текст).

## Переписывание хендлеров (карта)

Все 10 роутеров (`common, registration, new_driver, drivers, cars, schedules, payments,
reports, ai_query, start`) — заменить прямые вызовы `X_service(session, ...)` на `await
api.<метод>(...)`. Примеры соответствий:
- `cars.py`: `cars_service.create_car/list_cars/...` → `api.create_car/cars/...`.
- `payments.py`: `receipt_hash/is_duplicate/recognize_receipt/create_payment/apply_payment` →
  бот качает файл → `api.recognize_receipt(bytes)` → показывает подтверждение → `api.create_payment(...)`
  (дедуп/долг считает core-api). Уведомление владельцу — core-api может вернуть флаг/данные, а
  сам текст владельцу шлёт бот (он владеет `bot.send_message`), либо владельцу тоже идёт через
  механизм алертов/дайджеста.
- `schedules.py`: `set_schedule/schedule_status` → `api.set_schedule/get_schedule` (DTO со
  статусом). UI-строки (`_due_line`: «Срок сегодня»/«просрочен на N дн.») — оставить в боте,
  считать по данным из DTO.
- `ai_query.py`: `build_snapshot + answer_owner_query` → `api.assistant_query(question)`.
- `registration/new_driver/drivers/invitations`: через соответствующие эндпоинты; повторная
  валидация инвайта (дважды — при открытии и при отправке селфи) сохраняется, но проверку делает
  core-api (`resolve_invitation`).

Важно сохранить наблюдаемое поведение (тексты, кнопки, FSM) — эталон в `ИНСТРУКЦИЯ_АДМИНА.md`,
`QA_ИНСТРУКЦИЯ.md` и dispatch-тестах. Меню, форматирование денег, нормализация номера
(uppercase) — как было. **TTL инвайта считает core-api** (переехал туда, см. 01); бот лишь
показывает `expires_at`/метку из ответа API.

## Планировщик бота (`scheduler.py`)

Остаётся APScheduler, но джобы работают через API:
- **Ежедневные напоминания** (09:00 `TZ`): `plan = api.reminders_plan(now)`; разослать
  `plan.reminders` водителям и `plan.owner_digest` — админам; затем `api.reminders_mark(...)`.
- **Опрос алертов** (напр. каждые 60–120с): `alerts = api.alerts(status="open")`; для новых —
  карточка админам. **Карточка и кнопки зависят от `alert.type`** (T2 — не на каждый алерт кнопка
  «Заблокировать»):
  - rule-типы (`overdue_payment` / `fines_count` / `maintenance_km`) → текст правила +
    **[Заблокировать двигатель] [Отложить]**.
  - `command_unconfirmed` → «команда ушла, подтверждения нет — возможно, нет реле» +
    **[Повторить] [Понятно]**. Кнопки «Заблокировать» НЕТ (иначе повтор блокировки поверх
    неподтверждённой).
  - `odometer_untrusted` → «нужна переустановка базы пробега по <plate>» +
    **[ТО выполнено]** (или подсказка переустановить базу через привязку трекера). Кнопки
    «Заблокировать» НЕТ.
  Дедуп доставки — по id алерта (помнить отправленные, либо `api.ack_alert` после показа). См. 06.

## Callback для блокировки

Новый `CallbackData` (напр. `BlockCB(car_id, alert_id, action=block|unblock)`), `pack()` ≤ 64
байт (как остальные в `callbacks.py`). Хендлер: `api.command(car_id, type="engine_block",
requested_by=<tg_id>, alert_id=...)` → показать результат:
- заблокировано (подтверждено) / **«команда ушла, блокировка не подтверждена»** (status
  `unconfirmed` — возможно, нет реле) / **отказ гейта** («машина едет / зажигание вкл / нет
  свежих данных») / ошибка.
- **Сразу после нажатия убрать/задизейблить кнопку** (edit reply markup) — против двойного тапа;
  сервер тоже идемпотентен по `(alert_id, type)`, но UI не должен провоцировать повтор.
Кнопка «Разблокировать» → `type="engine_unblock"`.

**Уведомление водителя (обязательно):** при успешной блокировке/разблокировке бот шлёт водителю
машины сообщение (кто/почему/что делать). Данные для адресации (tg_user_id водителя) берём из
`api.car(id)`/`api.driver(...)`. Это часть потока, не опция (UX + юридика арендных машин).

## Тесты бота

Переписать dispatch-тесты (`test_dispatch.py`, `test_receipt_input.py`) на **мок `ApiClient`**
(вместо мока сервисов/сессии). Проверять, что хендлеры дергают правильные методы API и
корректно рендерят ответы/кнопки. Доменные тесты (schedules/payments/…) уезжают в core-api.
