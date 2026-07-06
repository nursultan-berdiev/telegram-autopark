"""Тесты автопарка (Этап 1)."""
from bot.db.models import CarStatus
from bot.services import cars as cars_service


async def test_create_and_list(session):
    c1 = await cars_service.create_car(
        session, plate="01A123BC", model="Cobalt", photo_file_id="f", photo_path="p"
    )
    assert c1.id and c1.status == CarStatus.free
    assert len(await cars_service.list_cars(session)) == 1


async def test_plate_exists(session):
    await cars_service.create_car(
        session, plate="AA", model=None, photo_file_id=None, photo_path=None
    )
    assert await cars_service.plate_exists(session, "AA") is True
    assert await cars_service.plate_exists(session, "ZZ") is False


async def test_free_filter_and_delete(session):
    c1 = await cars_service.create_car(
        session, plate="AA", model=None, photo_file_id=None, photo_path=None
    )
    c2 = await cars_service.create_car(
        session, plate="BB", model=None, photo_file_id=None, photo_path=None
    )
    c1.status = CarStatus.occupied
    await session.commit()
    free = await cars_service.list_free_cars(session)
    assert [c.id for c in free] == [c2.id]

    await cars_service.delete_car(session, c2)
    assert len(await cars_service.list_cars(session)) == 1
