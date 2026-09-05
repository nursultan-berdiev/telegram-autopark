"""HTTP-клиент к core-api: единственный способ боту получить данные.

Прямого доступа к БД у бота больше нет — вся доменная логика за HTTP.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_HUMAN_BY_STATUS = {
    401: "Нет доступа к серверу (проверьте токен).",
    403: "Действие доступно только администратору.",
    404: "Не найдено.",
    409: "Действие невозможно.",
    422: "Некорректные данные.",
}


class ApiError(Exception):
    """Ошибка API с человеческим текстом для показа пользователю."""

    def __init__(self, status_code: int, detail: str | None = None) -> None:
        self.status_code = status_code
        self.detail = detail or _HUMAN_BY_STATUS.get(status_code, "Ошибка сервера.")
        super().__init__(f"{status_code}: {self.detail}")

    @property
    def human(self) -> str:
        return self.detail


def _json_ready(value: Any) -> Any:
    """Decimal и datetime не сериализуются json'ом по умолчанию."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_ready(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_json_ready(v) for v in value]
    return value


class ApiClient:
    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self._base_url = (base_url or settings.core_api_url).rstrip("/")
        self._token = token or settings.core_api_token
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=settings.api_timeout_seconds,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        files: Any = None,
        tg_id: int | None = None,
    ) -> Any:
        client = await self._http()
        headers = {"X-TG-User-Id": str(tg_id)} if tg_id is not None else None
        try:
            response = await client.request(
                method,
                path,
                params=_json_ready(params) if params else None,
                json=_json_ready(json) if json is not None else None,
                files=files,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            log.warning("core-api недоступен: %s", exc)
            raise ApiError(503, "Сервер недоступен, попробуйте позже.") from exc

        if response.status_code >= 400:
            detail = None
            try:
                detail = response.json().get("detail")
            except Exception:  # noqa: BLE001 — тело может быть не json
                detail = None
            raise ApiError(response.status_code, detail)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # --- Роли ----------------------------------------------------------------
    async def me(self, tg_id: int) -> dict:
        return await self._request("GET", "/me", params={"tg_id": tg_id})

    # --- Машины --------------------------------------------------------------
    async def cars(self, *, free: bool = False) -> list[dict]:
        return await self._request("GET", "/cars", params={"free": 1} if free else None)

    async def car(self, car_id: int) -> dict:
        return await self._request("GET", f"/cars/{car_id}")

    async def create_car(
        self,
        *,
        plate: str,
        model: str | None = None,
        photo_file_id: str | None = None,
        photo_path: str | None = None,
    ) -> dict:
        return await self._request(
            "POST",
            "/cars",
            json={
                "plate": plate,
                "model": model,
                "photo_file_id": photo_file_id,
                "photo_path": photo_path,
            },
        )

    async def delete_car(self, car_id: int) -> None:
        await self._request("DELETE", f"/cars/{car_id}")

    # --- Водители ------------------------------------------------------------
    async def drivers(self, *, active: bool = True) -> list[dict]:
        return await self._request("GET", "/drivers", params={"active": int(active)})

    async def driver(self, driver_id: int) -> dict:
        return await self._request("GET", f"/drivers/{driver_id}")

    async def register_driver(self, **payload: Any) -> dict:
        return await self._request("POST", "/drivers/register", json=payload)

    async def fire_driver(self, driver_id: int, *, tg_id: int | None = None) -> dict:
        return await self._request("POST", f"/drivers/{driver_id}/fire", tg_id=tg_id)

    # --- Приглашения ---------------------------------------------------------
    async def create_invitation(self, car_id: int, *, created_by: int) -> dict:
        return await self._request(
            "POST",
            "/invitations",
            json={"car_id": car_id, "created_by": created_by},
            tg_id=created_by,
        )

    async def resolve_invitation(self, code: str) -> dict:
        return await self._request("GET", "/invitations/resolve", params={"code": code})

    # --- Графики -------------------------------------------------------------
    async def get_schedule(self, driver_id: int) -> dict:
        return await self._request("GET", f"/drivers/{driver_id}/schedule")

    async def set_schedule(
        self,
        driver_id: int,
        *,
        period: str,
        amount: Decimal,
        next_due_date: datetime,
        interval_days: int | None = None,
    ) -> dict:
        return await self._request(
            "PUT",
            f"/drivers/{driver_id}/schedule",
            json={
                "period": period,
                "amount": amount,
                "next_due_date": next_due_date,
                "interval_days": interval_days,
            },
        )

    # --- Платежи -------------------------------------------------------------
    async def recognize_receipt(
        self, data: bytes, media_type: str, filename: str = "receipt"
    ) -> dict:
        return await self._request(
            "POST",
            "/payments/recognize",
            files={"file": (filename, data, media_type)},
        )

    async def create_payment(self, **payload: Any) -> dict:
        return await self._request("POST", "/payments", json=payload)

    async def payments(self, driver_id: int) -> list[dict]:
        return await self._request("GET", f"/drivers/{driver_id}/payments")

    # --- Отчёты и ассистент --------------------------------------------------
    async def report_cars_drivers(self) -> list[dict]:
        return await self._request("GET", "/reports/cars-drivers")

    async def report_upcoming(self, days: int = 7) -> list[dict]:
        return await self._request("GET", "/reports/upcoming", params={"days": days})

    async def report_by_driver(self) -> list[dict]:
        return await self._request("GET", "/reports/by-driver")

    async def report_by_car(self) -> list[dict]:
        return await self._request("GET", "/reports/by-car")

    async def assistant_query(self, question: str) -> dict:
        return await self._request("POST", "/assistant/query", json={"question": question})

    # --- Напоминания ---------------------------------------------------------
    async def reminders_plan(
        self, now: datetime | None = None, *, force: bool = False
    ) -> dict:
        params: dict = {}
        if now is not None:
            params["now"] = now
        if force:
            params["force"] = 1
        return await self._request("GET", "/reminders/plan", params=params or None)

    async def reminders_mark(
        self, schedule_ids: list[int], on_date: date | None = None
    ) -> None:
        """Дату по умолчанию ставит сервер — у него и живёт таймзона парка."""
        payload: dict = {"schedule_ids": schedule_ids}
        if on_date is not None:
            payload["on_date"] = on_date
        await self._request("POST", "/reminders/mark", json=payload)

    # --- Телеметрия и трекеры ------------------------------------------------
    async def car_state(self, car_id: int) -> dict:
        return await self._request("GET", f"/cars/{car_id}/state")

    async def car_telemetry(
        self,
        car_id: int,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[dict]:
        params: dict = {"limit": limit}
        if since is not None:
            params["from"] = since
        if until is not None:
            params["to"] = until
        return await self._request("GET", f"/cars/{car_id}/telemetry", params=params)

    async def get_tracker(self, car_id: int) -> dict | None:
        return await self._request("GET", f"/cars/{car_id}/tracker")

    async def set_tracker(
        self, car_id: int, *, external_id: str, provider: str = "traccar"
    ) -> dict:
        return await self._request(
            "PUT",
            f"/cars/{car_id}/tracker",
            json={"provider": provider, "external_id": external_id},
        )

    # --- Штрафы и ТО ---------------------------------------------------------
    async def fines(self, car_id: int) -> list[dict]:
        return await self._request("GET", f"/cars/{car_id}/fines")

    async def add_fine(self, car_id: int, *, tg_id: int, **payload: Any) -> dict:
        return await self._request(
            "POST", f"/cars/{car_id}/fines", json=payload, tg_id=tg_id
        )

    async def pay_fine(self, fine_id: int, *, tg_id: int) -> dict:
        return await self._request("POST", f"/fines/{fine_id}/pay", tg_id=tg_id)

    async def maintenance(self, car_id: int) -> list[dict]:
        return await self._request("GET", f"/cars/{car_id}/maintenance")

    async def set_maintenance(self, car_id: int, *, tg_id: int, **payload: Any) -> dict:
        return await self._request(
            "PUT", f"/cars/{car_id}/maintenance", json=payload, tg_id=tg_id
        )

    async def maintenance_done(self, car_id: int, mtype: str, *, tg_id: int) -> dict:
        return await self._request(
            "POST", f"/cars/{car_id}/maintenance/{mtype}/done", tg_id=tg_id
        )

    # --- Алерты и команды ----------------------------------------------------
    async def alerts(self, status: str = "open") -> list[dict]:
        return await self._request("GET", "/alerts", params={"status": status})

    async def ack_alert(self, alert_id: int) -> dict:
        return await self._request("POST", f"/alerts/{alert_id}/ack")

    async def resolve_alert(self, alert_id: int) -> dict:
        return await self._request("POST", f"/alerts/{alert_id}/resolve")

    async def command(
        self, car_id: int, *, type: str, requested_by: int, alert_id: int | None = None
    ) -> dict:
        return await self._request(
            "POST",
            f"/cars/{car_id}/commands",
            json={"type": type, "requested_by": requested_by, "alert_id": alert_id},
            tg_id=requested_by,
        )

    async def commands(self, car_id: int) -> list[dict]:
        return await self._request("GET", f"/cars/{car_id}/commands")
