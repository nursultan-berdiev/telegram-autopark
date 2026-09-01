# 04 — tracker-adapter (FastAPI): слой трекеров

Расширяемый слой к трекинг-платформам. Хостится **отдельно** (рядом с Traccar). Наружу торчит
только он (не Traccar). Своей БД нет — stateless-транслятор. Две функции:
1. **Ингест**: тянет телеметрию из Traccar (WebSocket) → нормализует → батч-POST в core-api
   `/telemetry/batch`.
2. **Команды**: принимает от core-api команду на машину/устройство → шлёт в Traccar (REST).

## Интерфейс провайдера (`app/providers/base.py`)

Точка расширяемости (req 1). Новый тип трекера = новый класс, реализующий интерфейс. core-api
об этом не знает — он всегда говорит с адаптером единообразно; провайдер выбирается по
`provider` из привязки трекера.

```python
class TrackerCommand(str, Enum):
    ENGINE_STOP = "engine_stop"; ENGINE_RESUME = "engine_resume"
    ALARM_ARM = "alarm_arm";     ALARM_DISARM = "alarm_disarm"

@dataclass
class NormalizedPoint:          # → libs/contracts.TelemetryPoint
    external_id: str; ts: datetime; server_ts: datetime
    lat: float|None; lon: float|None; speed_knots: float|None; course: float|None
    altitude: float|None; valid: bool
    ignition: bool|None; motion: bool|None
    total_distance_km: Decimal|None; engine_blocked: bool|None
    status_raw: str|None; attributes: dict

class TrackerProvider(ABC):
    name: str
    @abstractmethod async def list_devices(self) -> list[dict]: ...
    @abstractmethod async def get_state(self, external_id: str) -> NormalizedPoint|None: ...
    @abstractmethod async def stream(self) -> AsyncIterator[NormalizedPoint]: ...   # живой поток
    @abstractmethod async def send_command(self, external_id: str, cmd: TrackerCommand,
                                           params: dict|None=None) -> dict: ...      # {status,result}
```

`app/providers/registry.py`: `get_provider(name) -> TrackerProvider`. v1: только `"traccar"`.

## TraccarProvider (`app/providers/traccar.py`)

Общается с Traccar по REST + WebSocket. Все факты (эндпоинты, коды команд, биты) — в
[09-traccar-reference.md](09-traccar-reference.md). Не угадывать.

**Аутентификация**: `POST /api/session` формой `email=&password=` → cookie сессии
(держать в httpx-клиенте). Либо Bearer-token Traccar, если заведём. Реконнект при 401.

**Команды** (`send_command`): `POST /api/commands/send` c JSON
`{"deviceId": <traccar numeric id>, "type": "<traccar type>", ...}`. Маппинг наших команд →
Traccar (подтверждено на пилоте):
```
ENGINE_STOP    -> engineStop     (на проводе S20,1,1)
ENGINE_RESUME  -> engineResume   (S20,1,0)
ALARM_ARM      -> alarmArm        (SCF,0,0)
ALARM_DISARM   -> alarmDisarm     (SCF,1,1)
```
`external_id` (uniqueId, напр. `9175358042`) → Traccar numeric `deviceId` через `GET /api/devices`
(кэшировать map uniqueId→id). Возврат: статус доставки + ответ трекера (напр. `S20,OK`).
Замечание: `positionPeriodic` (S71) на ST-901M **не поддерживается** — не использовать.

**Ингест** (`stream`): подключиться к Traccar WebSocket `/api/socket` (после `/api/session`).
Он шлёт JSON с ключами `positions`, `devices`, `events`. Для каждой позиции построить
`NormalizedPoint`:
- `lat=latitude, lon=longitude, speed_knots=speed, course, altitude, valid`.
- `ts=deviceTime`, `server_ts=now`.
- `ignition = attributes.ignition` (Traccar уже извлекает из статус-маски; = бит 10).
- `motion = attributes.motion`.
- `total_distance_km = attributes.totalDistance / 1000` (Traccar даёт метры).
- `status_raw = attributes.status` (hex), `engine_blocked` = бит 27 статус-маски (0=заблокирован
  → engine_blocked=true). Вычислять из числового status: `blocked = not (status >> 27 & 1)`.
- `attributes` = io1..io4, protocol, raw и т.п.
Фолбэк, если WS недоступен: polling `GET /api/positions` раз в `POLL_INTERVAL_SECONDS` (флаг `TRACCAR_WS_ENABLED=0`).

## Ингест-воркер (`app/ingest.py`)

Запускается в `lifespan` FastAPI как фоновая задача:
- Держит `provider.stream()`; копит `NormalizedPoint` в буфер; флашит батчами
  (`TELEMETRY_BATCH_SIZE` или каждые `TELEMETRY_FLUSH_SECONDS`) в core-api
  `POST /telemetry/batch` (Bearer `INGEST_TOKEN` — отдельный от `CORE_API_TOKEN`, права только на
  ингест).
- Реконнект с backoff при разрыве WS/сессии. Ошибку core-api не терять — ретрай буфера.
- Метрика/лог: сколько точек, задержка, разрывы.

## Command API адаптера (`app/commands.py`, входящие от core-api)

```
POST /devices/{external_id}/commands   (Bearer ADAPTER_TOKEN)
  body: {type: engine_stop|engine_resume|alarm_arm|alarm_disarm, params?}
  -> {status: sent|failed, result: "<traccar/tracker ответ>"}
GET  /devices/{external_id}/state       -> NormalizedPoint|null   (fallback для core-api)
GET  /health                            -> {ok, traccar: connected?, ingest: running?}
```
Адаптер **не** делает защитный гейт — это доменное решение core-api (см. 06). Адаптер тупо
исполняет команду и возвращает результат.

## Конфиг (env)

`CORE_API_URL`, `INGEST_TOKEN`, `ADAPTER_TOKEN`, `TRACCAR_URL`, `TRACCAR_USER`,
`TRACCAR_PASSWORD`, `TRACCAR_WS_ENABLED=1`, `TELEMETRY_BATCH_SIZE`, `TELEMETRY_FLUSH_SECONDS`,
`POLL_INTERVAL_SECONDS` (фолбэк).

## Зависимости

`fastapi`, `uvicorn[standard]`, `httpx`, `websockets` (или httpx-ws), `pydantic-settings`,
`libs/contracts`. БД-драйверов нет.

## Расширение под новый трекер (памятка)

1. Новый класс в `app/providers/<vendor>.py`, реализующий `TrackerProvider`.
2. Зарегистрировать в `registry.py`, добавить значение в enum `TrackerProvider` (миграция в
   core-api).
3. core-api и бот не трогаются — привязка трекера просто указывает новый `provider`.
