"""Тесты приглашений и регистрации (Этап 2)."""
from datetime import datetime, timedelta, timezone

from bot.db.models import CarStatus, InviteStatus
from bot.services import cars as cars_service
from bot.services import drivers as drivers_service
from bot.services import invitations as inv_service


async def _make_car(session, plate="AA"):
    return await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )


async def test_invitation_lifecycle(session):
    car = await _make_car(session)
    inv = await inv_service.create_invitation(session, car_id=car.id, created_by=1)
    assert inv.status == InviteStatus.active

    found = await inv_service.get_valid_invitation(session, inv.code)
    assert found is not None and found.id == inv.id
    assert await inv_service.get_valid_invitation(session, "nope") is None


async def test_registration_occupies_car(session):
    car = await _make_car(session)
    driver = await drivers_service.register_driver(
        session, tg_user_id=999, full_name="Иванов Иван", phone="+998901234567",
        inn="12345678", selfie_file_id="s", selfie_path="p", car_id=car.id,
    )
    assert driver.car_id == car.id
    car_after = await cars_service.get_car(session, car.id)
    assert car_after.status == CarStatus.occupied
    assert len(await cars_service.list_free_cars(session)) == 0


async def test_used_invitation_invalid(session):
    car = await _make_car(session)
    inv = await inv_service.create_invitation(session, car_id=car.id, created_by=1)
    await inv_service.mark_used(session, inv, used_by=42)
    assert inv.status == InviteStatus.used
    assert await inv_service.get_valid_invitation(session, inv.code) is None


async def test_expired_invitation(session):
    car = await _make_car(session)
    inv = await inv_service.create_invitation(session, car_id=car.id, created_by=1)
    inv.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await session.commit()
    assert await inv_service.get_valid_invitation(session, inv.code) is None
    assert inv.status == InviteStatus.expired
