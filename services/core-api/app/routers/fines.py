"""Роутер fines: штрафы автомобиля (см. plan/03 §«Штрафы и ТО»)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor, require_core, require_import_actor
from app.db.models import Fine
from app.db.session import get_session
from app.domain import cars as cars_service
from app.domain import fines as fines_service
from app.domain import periodic as periodic_service
from app.errors import DomainError, NotFound
from app.routers.periodic import run_dto
from app.tasks import fines_tolom
from app.tasks.celery_app import celery_app
from contracts import FineCreate, FineDTO, FineImportItem, FineImportResult, TaskRunDTO

router = APIRouter()


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _fine_dto(fine: Fine, plate: str | None = None) -> FineDTO:
    return FineDTO(
        id=fine.id,
        car_id=fine.car_id,
        driver_id=fine.driver_id,
        amount=fine.amount,
        amount_to_pay=fine.amount_to_pay,
        discount_days_left=fine.discount_days_left,
        currency=fine.currency,
        issued_at=_utc(fine.issued_at),
        status=fine.status.value,
        paid_at=_utc(fine.paid_at),
        source=fine.source,
        external_ref=fine.external_ref,
        article=fine.article,
        violation_title=fine.violation_title,
        place=fine.place,
        payment_code=fine.payment_code,
        protocol_kind=fine.protocol_kind,
        delivery_date=fine.delivery_date,
        discount_until=fine.discount_until,
        last_seen_at=_utc(fine.last_seen_at),
        last_seen_source=fine.last_seen_source,
        paid_by=fine.paid_by,
        note=fine.note,
        car_plate=plate,
        created_at=_utc(fine.created_at),
    )


@router.get("/cars/{car_id}/fines", response_model=list[FineDTO])
async def list_car_fines(
    car_id: int,
    only_unpaid: bool = False,
    window_days: int | None = None,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[FineDTO]:
    car = await cars_service.get_car(session, car_id)
    if car is None:
        raise NotFound(f"машина {car_id} не найдена")
    fines = await fines_service.list_fines(
        session, car_id, only_unpaid=only_unpaid, window_days=window_days
    )
    return [_fine_dto(f) for f in fines]


@router.post("/cars/{car_id}/fines", response_model=FineDTO, status_code=201)
async def add_car_fine(
    car_id: int,
    payload: FineCreate,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(require_admin_actor),
) -> FineDTO:
    car = await cars_service.get_car(session, car_id)
    if car is None:
        raise NotFound(f"машина {car_id} не найдена")
    fine = await fines_service.add_fine(
        session,
        car_id,
        driver_id=payload.driver_id,
        amount=payload.amount,
        currency=payload.currency,
        issued_at=payload.issued_at,
        external_ref=payload.external_ref,
        note=payload.note,
        created_by=actor,
    )
    return _fine_dto(fine)


@router.get("/fines/import/plates", response_model=list[str])
async def import_plates(
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_import_actor),
) -> list[str]:
    """Номера парка для раннера: узкому токену не нужен доступ ко всему /cars."""
    return await cars_service.list_plates(session)


@router.post("/fines/import", response_model=FineImportResult)
async def import_car_fines(
    items: list[FineImportItem],
    source: str = "carcheck",
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(require_import_actor),
) -> FineImportResult:
    """Пакетная заливка найденных снаружи штрафов: повторный прогон безопасен."""
    # Домен не должен знать о транспортных DTO — как в routers/telemetry.py.
    rows = [
        fines_service.FineImportRow(
            plate=item.plate,
            external_ref=item.external_ref,
            amount=item.amount,
            amount_to_pay=item.amount_to_pay,
            discount_days_left=item.discount_days_left,
            currency=item.currency,
            issued_at=item.issued_at,
            note=item.note,
        )
        for item in items
    ]
    outcome = await fines_service.import_fines(
        session, rows, source=source, created_by=actor
    )
    # Разбивка по номерам — для внутренних вызовов, в контракт не идёт.
    return FineImportResult(
        created=outcome.created,
        skipped=outcome.skipped,
        unknown_plates=outcome.unknown_plates,
        ambiguous_plates=outcome.ambiguous_plates,
    )


@router.get("/fines", response_model=list[FineDTO])
async def list_fleet_fines(
    only_unpaid: bool = True,
    car_id: int | None = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> list[FineDTO]:
    """Штрафы всего парка одним запросом — экран «все штрафы» у админа."""
    rows = await fines_service.list_fleet_fines(
        session, car_id=car_id, only_unpaid=only_unpaid, limit=min(limit, 500)
    )
    return [_fine_dto(fine, plate) for fine, plate in rows]


@router.post("/fines/check", response_model=TaskRunDTO, status_code=202)
async def check_fines_now(
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(require_admin_actor),
) -> TaskRunDTO:
    """Ставит проверку парка в очередь и отдаёт прогон, за которым следить.

    Строка прогона заводится ЗДЕСЬ, а не в воркере: иначе свой прогон не
    отличить от кронового, начавшегося в ту же секунду.
    """
    # Второй тап не должен ни копить очередь при concurrency=1, ни лишний раз
    # ходить в госсервис. Отсекает это уникальный индекс внутри start_run, а не
    # проверка перед вставкой: между проверкой и вставкой помещается второй
    # запрос.
    run, created = await periodic_service.start_run(
        session, task=fines_tolom.NAME, requested_by=actor
    )
    if not created:
        return run_dto(run)
    try:
        celery_app.send_task(
            fines_tolom.NAME, kwargs={"run_id": run.id}, retry=False
        )
    except Exception as exc:  # брокер лежит — прогон не начнётся никогда
        run.detail = f"очередь недоступна: {type(exc).__name__}"
        run.finished_at = datetime.now(timezone.utc)
        await session.commit()
        raise DomainError("очередь задач недоступна", status_code=503) from exc
    return run_dto(run)


@router.get("/fines/{fine_id}", response_model=FineDTO)
async def get_car_fine(
    fine_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> FineDTO:
    """Карточка штрафа — только из нашей БД, без похода в сервис."""
    found = await fines_service.get_fine_with_plate(session, fine_id)
    if found is None:
        raise NotFound("штраф не найден")
    return _fine_dto(*found)


@router.post("/fines/{fine_id}/pay", response_model=FineDTO)
async def pay_car_fine(
    fine_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> FineDTO:
    fine = await fines_service.pay_fine(session, fine_id)
    if fine is None:
        raise NotFound(f"штраф {fine_id} не найден")
    return _fine_dto(fine)


@router.delete("/fines/{fine_id}", status_code=204, response_model=None)
async def delete_car_fine(
    fine_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> None:
    deleted = await fines_service.delete_fine(session, fine_id)
    if not deleted:
        raise NotFound(f"штраф {fine_id} не найден")
