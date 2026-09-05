# 06 — Движок правил, алерты, блокировка двигателя

Реализуется в core-api. Решение заказчика: **блокировка только по алерту вручную**; автономной
immobilization нет. На само действие блокировки — **защитный гейт**.

## Модель правил (расширяемость)

Правило = запись в `rules` (см. 02): `type`, `params JSONB`, `car_id` (NULL = все), `enabled`,
`severity`. Оценщик (`app/rules/engine.py`) периодически (APScheduler, `RULES_INTERVAL_SECONDS`)
проходит по включённым правилам × применимым машинам и вызывает per-type evaluator
(`app/rules/evaluators.py`). Новый тип правила = новый evaluator + значение enum `RuleType`.

Каждый evaluator — чистая функция:
```
def evaluate(session, car, rule) -> RuleHit | None
RuleHit = {triggered: bool, payload: dict, human: str}
```

### v1-эвалуаторы

**overdue_payment** (`params: {"min_debt": <Decimal>=0, "min_days": <int>=1}`)
- Взять график водителя машины, посчитать `schedule_status(now)` (**переиспользовать
  `domain/schedules.py` без изменений**). Триггер если `is_overdue and overdue_days >= min_days
  and debt_now >= min_debt`. payload: `{debt, overdue_days, next_due_date}`.

**fines_count** (`params: {"count": N, "window_days": W|null}`)
- Считать **только неоплаченные** штрафы (`fines.status == unpaid`) за окно (или все, если
  window=null). Триггер если `count(unpaid) >= N`. payload: `{unpaid_count, window_days}`.
- Условие снимается (→ авто-resolve), когда админ переводит штрафы в `paid` и `unpaid < N`.
  Без статуса `unpaid`/`paid` алерт висел бы вечно — поэтому статус в модели обязателен (см. 02).

**maintenance_km** (`params: {"grace_km": G>=0}`, применяется к `maintenance_items` машины)
- Для каждого item: `over = car_state.odometer_km - last_service_km - interval_km`. Триггер если
  `over >= grace_km`. payload: `{type, over_km, odometer_km, interval_km, odometer_tracker_id}`
  (от какого трекера считали — R8). Дергать также из `/telemetry/batch` (быстрая реакция на
  пробег), не только по таймеру.
- ⚠️ Одометр = пробег ТРЕКЕРА, не авто (см. 02). База достоверна, только если:
  `car_state.odometer_tracker_id == maintenance_items.last_service_tracker_id` (S1 — сравнивать
  именно с этим полем) **И** `car_state.odometer_trusted == true` (S2 — не было немонотонного
  сброса счётчика).
- Если база недостоверна — **не молча пропускать** (это скрытая потеря функции, S3), а поднять
  **системный info-алерт `odometer_untrusted`** («по машине X требуется переустановка базы
  пробега»). **Только если у машины есть хотя бы один `maintenance_item`** (T3) — иначе ТО по ней
  не ведётся и алерт бессмыслен. Правило `maintenance_km` при этом не считать. Доверие возвращает только явная
  переустановка базы (`maintenance/{type}/done` или `PUT /cars/{id}/tracker`), которая проставляет
  `last_service_km`, `last_service_tracker_id` и сбрасывает `odometer_trusted=true`; после этого
  `odometer_untrusted`-алерт авто-resolve.

## Алерты (жизненный цикл)

- Триггер → создать `alert(status=open)` через `INSERT ... ON CONFLICT DO UPDATE SET payload=...`
  (дедуп обеспечивают **два частичных индекса** из 0008, см. 02): rule-алерты — по
  `(rule_id, car_id)`; **системные (rule_id=NULL) — по `(car_id, type)`**. Надёжно против гонки
  нескольких точек входа (таймер движка, `/telemetry/batch`, command-timeout).
- **При конфликте payload ОБНОВЛЯТЬ, не `DO NOTHING`** — иначе второй `command_unconfirmed` по
  той же машине (уже другая команда) проглотится без следа. Открытый алерт всегда несёт свежий
  снимок (последняя неподтверждённая команда / актуальный долг / текущий пробег).
- `triggered_at` при этом **неизменен** (момент первого срабатывания — «висит с …»); свежесть
  снимка отражает `last_seen_at`, обновляемый на каждом повторе (U1).
- Условие перестало выполняться (оплатил / ТО сделано / штрафы обнулились) → авто-`resolved`.
- `GET /alerts?status=open` — бот опрашивает и показывает админам; `POST /alerts/{id}/ack`
  (показан) / `/resolve` (закрыт вручную).
- Дедуп доставки в боте — по id алерта (не слать одно и то же повторно).

## Блокировка двигателя — поток (ручной, с гейтом)

```
rule.engine → alert(open)
   → бот (опрос) показывает АДМИНУ: «⚠️ <текст> по <plate>. [Заблокировать] [Отложить]»
   → админ жмёт [Заблокировать]  (кнопка сразу дизейблится в UI — против двойного тапа)
   → bot: POST core-api /cars/{id}/commands {type: engine_block, requested_by: <tg_id>, alert_id}
   → core-api AUTH: requested_by ∈ ADMIN_IDS? нет → 403 (клиенту не доверяем)
   → core-api IDEMPOTENCY: есть ли по (alert_id, type) команда за COMMAND_DEDUP_SECONDS / в статусе
        queued|sent|acked? да → вернуть её же, новую НЕ слать
   → core-api SAFETY GATE (по свежему car_state):
        online(last_ts свежий) AND speed_knots < 1 AND motion == false AND ignition == false
        ├─ НЕ выполнено → command(status=blocked_by_safety, safety_snapshot=...),
        │                 отказ с причиной («машина в движении / зажигание вкл / нет свежей
        │                 телеметрии — блокировка отложена»)
        └─ выполнено → adapter POST /devices/{external_id}/commands {engine_stop}
                       → command(status=sent, result=<ответ трекера>), alert.action_taken="engine_block"
   → ПОДТВЕРЖДЕНИЕ: ждём телеметрию с битом 27 (engine_blocked=true) в окно COMMAND_ACK_WINDOW_SECONDS
        ├─ пришло → command(status=acked, acked_at=...), car_state.engine_blocked=true
        └─ не пришло в окно (в т.ч. трекер замолчал/оффлайн) → отдельная джоба переводит в
             command(status=unconfirmed) + предупреждение админу (см. «Досрочивание команд» ниже)
             («команда ушла, но блокировка не подтверждена телеметрией — возможно, нет реле»)
   → бот уведомляет ВОДИТЕЛЯ о блокировке (и о разблокировке); кнопка [Разблокировать]
     → type=engine_unblock (engine_resume)
```

### Защитный гейт — детали (обязателен даже при ручном решении)
- В core-api при обработке `engine_block`. Данные — из `car_state`, `online` **вычисляется из
  `last_ts`** (не хранимая колонка, см. 02): нет свежей телеметрии → небезопасно.
- Условие остановки — **не строгое `==0`** (GPS стоящей машины шумит): `speed_knots < 1` (узел)
  **И** `motion == false` **И** `ignition == false`. Поле `motion` в модели есть.
- Отказ по гейту — `command(status=blocked_by_safety)` + понятное сообщение; это **не ошибка**.
  Автоповтора нет (решение ручное) — админ повторит, когда машина встанет.
- `engine_unblock` гейтом не ограничиваем (разблокировать безопасно всегда).

### Переход sent → acked / unconfirmed (кто и когда ставит)
`sent` — команда доставлена адаптеру/Traccar (получен ответ трекера, напр. `S20,OK`). Два пути
перехода — оба обязательны:
1. **acked** — обработчик телеметрии (`/telemetry/batch`), когда впервые видит бит 27
   (engine_blocked) у машины в окно `COMMAND_ACK_WINDOW_SECONDS` после `sent`.
2. **unconfirmed** — **отдельная периодическая джоба** (см. «Досрочивание команд»): телеметрии
   может не быть вовсе (трекер замолчал/оффлайн — самый вероятный «что-то пошло не так»), тогда
   обработчик п.1 не вызовется и никто не переведёт статус. Джоба закрывает эту дыру.
Это ровно сценарий «`S20,OK` пришёл, а реле в машине нет» ИЛИ «трекер пропал» — не выдавать за
успешную блокировку. (Реле подтверждено только на 139.)

### Досрочивание команд (периодическая джоба, R1) + доставка (S4)
Джоба в core-api (APScheduler): команды в статусе `sent` старше `COMMAND_ACK_WINDOW_SECONDS`, у
которых не появился бит 27, → `unconfirmed`. Без неё unconfirmed не выставится при полном
отсутствии телеметрии.
**Доставка предупреждения (S4):** у core-api нет Telegram-канала (писать умеет только бот, а он
опрашивает `GET /alerts?status=open`). Поэтому джоба на `unconfirmed` **заводит системный алерт
`command_unconfirmed`** (severity=warning, rule_id=NULL, payload с car/command) — бот доставит
его тем же опросом. Не полагаться на «уведомить админа» без явного канала.

### Идемпотентность и авторизация команд
- **Идемпотентность:** дедуп по `(alert_id, type)` в окне `COMMAND_DEDUP_SECONDS` и/или пока предыдущая команда в
  статусе `queued|sent|acked`. Двойное нажатие кнопки не даёт двух `engineStop`. В боте — ещё и
  дизейбл кнопки после нажатия.
- **Авторизация:** только `requested_by ∈ ADMIN_IDS`, проверка **на сервере**; вызов доступен
  только по `CORE_API_TOKEN` (не по `INGEST_TOKEN`).
- **Уведомление водителя** — обязательный шаг потока (UX + юридика арендных машин), не опция.

### Аудит
Каждая команда → запись в `commands` с `safety_snapshot` (состояние на момент проверки),
`requested_by`, `alert_id`, `result`. Это юридически и операционно важно (кто/когда/почему
блокировал).

## Где что живёт

- `app/rules/engine.py` — планировщик + проход по правилам, создание/резолв алертов.
- `app/rules/evaluators.py` — три эвалуатора v1 (чистые, тестируемые на in-memory БД).
- `app/routers/rules.py`, `alerts.py`, `commands.py` — CRUD правил, чтение/ack/resolve алертов,
  выполнение команд с гейтом.
- `app/domain/commands.py` — логика гейта + вызов `clients/adapter.py`.

## Тесты (см. 08)
- Эвалуаторы: overdue (переиспользует schedule_status — покрыто), fines_count (считает только
  `unpaid`; границы N; окно), maintenance_km (границы grace).
- Дедуп алертов: гонка двух точек входа не плодит второй open (частичный uniq-индекс);
  авто-resolve при снятии условия (в т.ч. штраф → `paid`).
- **Гейт блокировки** (критично): отказ при `speed_knots>=1`, при `motion=true`, при
  `ignition on`, при несвежей телеметрии; успех только при `speed<1 ∧ motion=false ∧
  ignition=false ∧ online`; фиксация `commands.status` и `safety_snapshot`.
- Авторизация: неадмин `requested_by` → 403, команда не ушла.
- Идемпотентность: повторный `engine_block` по тому же `(alert_id,type)` не шлёт вторую команду.
- Подтверждение: `sent→acked` при телеметрии с битом 27 в окно; иначе `unconfirmed` +
  предупреждение (сценарий «нет реле, а S20,OK пришёл»).
- Поток команды: мок adapter-клиента; проверка записи `commands`, `alert.action_taken` и факта
  уведомления водителя.
