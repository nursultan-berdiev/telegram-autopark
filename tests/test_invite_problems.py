"""Отказ по ссылке-приглашению должен называть настоящую причину (PJ-13, п. 3–4).

Раньше на любой отказ бот отвечал «срок действия истёк» — и водитель, чью машину
уже занял другой, шёл просить новую ссылку туда, где машины больше нет.
"""
from datetime import datetime, timedelta, timezone

from bot.db.models import CarStatus
from bot.handlers.registration import INVITE_PROBLEM_TEXT
from bot.services import cars as cars_service
from bot.services import drivers as drivers_service
from bot.services import invitations as inv
from bot.services.invitations import InviteProblem


async def _car(session, plate="AA"):
    return await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )


async def test_ok(session):
    car = await _car(session)
    invite = await inv.create_invitation(session, car_id=car.id, created_by=1)

    check = await inv.resolve_invitation(session, invite.code)

    assert check.ok and check.problem is None
    assert check.invitation.id == invite.id


async def test_not_found(session):
    check = await inv.resolve_invitation(session, "выдуманный-код")
    assert check.problem is InviteProblem.not_found


async def test_expired(session):
    car = await _car(session)
    invite = await inv.create_invitation(session, car_id=car.id, created_by=1)
    invite.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await session.commit()

    check = await inv.resolve_invitation(session, invite.code)

    assert check.problem is InviteProblem.expired


async def test_used(session):
    car = await _car(session)
    invite = await inv.create_invitation(session, car_id=car.id, created_by=1)
    await inv.mark_used(session, invite, used_by=42)

    check = await inv.resolve_invitation(session, invite.code)

    assert check.problem is InviteProblem.used, "использованная ссылка — не «протухшая»"


async def test_car_taken_while_invite_still_alive(session):
    """Два приглашения на одну машину: второе живо, но машина уже занята.

    Именно этот случай раньше врал про «срок действия истёк».
    """
    car = await _car(session)
    first = await inv.create_invitation(session, car_id=car.id, created_by=1)
    second = await inv.create_invitation(session, car_id=car.id, created_by=1)

    # По первой ссылке зарегистрировался водитель — машина стала занята.
    await drivers_service.register_driver(
        session, tg_user_id=777, full_name="Первый Водитель", phone="+996",
        inn="12345678", selfie_file_id="s", selfie_path="p", car_id=car.id,
    )
    await inv.mark_used(session, first, used_by=777)
    assert (await cars_service.get_car(session, car.id)).status == CarStatus.occupied

    check = await inv.resolve_invitation(session, second.code)

    assert check.problem is InviteProblem.car_taken
    assert "занято другим водителем" in INVITE_PROBLEM_TEXT[check.problem]


def test_every_problem_has_text():
    """Ни одна причина не должна остаться без сообщения — иначе KeyError у водителя."""
    assert set(INVITE_PROBLEM_TEXT) == set(InviteProblem)
    assert all(text.strip() for text in INVITE_PROBLEM_TEXT.values())
