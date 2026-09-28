"""Рендер штрафов: подписи кнопок, карточка, страницы."""
from __future__ import annotations

from app.fines_view import (
    esc,
    fine_button_label,
    fine_card_text,
    list_header,
    money,
    page_slice,
    paginate,
    total_line,
)


def _fine(**kwargs) -> dict:
    base = {
        "id": 1,
        "status": "unpaid",
        "issued_at": "2026-08-30T13:08:59+06:00",
        "amount": "1000.00",
        "amount_to_pay": "300.00",
        "discount_until": "2026-09-30",
        "external_ref": "02-08-051-01-7-547822",
        "source": "tolom",
    }
    base.update(kwargs)
    return base


# --- подпись кнопки ---------------------------------------------------------


def test_label_shows_discount_and_full_price_after_it():
    assert fine_button_label(_fine()) == "300 сом (1 000 после 30.09.2026)"


def test_label_without_delivery_has_no_date():
    """Постановление не вручено — срок скидки не идёт, даты не существует."""
    assert (
        fine_button_label(_fine(discount_until=None))
        == "300 сом (1 000 после окончания скидки)"
    )


def test_label_without_discount_shows_one_number():
    assert fine_button_label(_fine(amount_to_pay=None)) == "1 000 сом"


def test_label_without_amount_says_so():
    """carcheck сумм не отдаёт — прочерк выглядел бы как ноль."""
    assert fine_button_label(_fine(amount=None, amount_to_pay=None)) == "сумма неизвестна"


def test_label_of_paid_fine():
    assert fine_button_label(_fine(status="paid")) == "1 000 сом · оплачен"


def test_label_can_lead_with_plate():
    """В списке по парку без номера машины кнопки неразличимы."""
    label = fine_button_label(_fine(car_plate="01KG195API"), with_plate=True)

    assert label.startswith("01KG195API · ")


# --- карточка ---------------------------------------------------------------


def test_card_has_everything_the_operator_pays_by():
    card = fine_card_text(
        _fine(
            car_plate="01KG195API",
            article="Ст. 187 ч. 1",
            violation_title="превышение скорости",
            place='а/д "Балыкчы-Каракол" 28.6-й км',
            payment_code="1000000000000000001",
        )
    )

    assert "02-08-051-01-7-547822" in card
    assert "01KG195API" in card
    assert "Ст. 187 ч. 1 — превышение скорости" in card
    assert "Балыкчы-Каракол" in card
    assert "1000000000000000001" in card
    assert "30.08.2026 13:08" in card


def test_card_says_when_the_paper_was_not_handed():
    card = fine_card_text(_fine())

    assert "не вручено" in card


def test_card_shows_delivery_date_when_known():
    card = fine_card_text(_fine(delivery_date="2026-09-01"))

    assert "вручено: 01.09.2026" in card


def test_card_distinguishes_machine_closure_from_human_one():
    """Мы не видели платежа — только заметили пропажу из ответа сервиса."""
    card = fine_card_text(_fine(status="paid", paid_by="tolom", paid_at="2026-09-06"))

    assert "пропал из tolom" in card


def test_card_escapes_service_text():
    """Одна угловая скобка от сервиса — и Telegram отвергнет сообщение целиком."""
    card = fine_card_text(_fine(place="<script>", violation_title="a & b"))

    assert "<script>" not in card
    assert "&lt;script&gt;" in card
    assert "a &amp; b" in card


def test_esc_keeps_plain_text_readable():
    assert esc("а/д \"Балыкчы\"") == 'а/д "Балыкчы"'


# --- деньги и итоги ---------------------------------------------------------


def test_money_groups_thousands_and_drops_kopecks():
    assert money("1000.00") == "1 000"
    assert money("300") == "300"
    assert money(None) is None


def test_total_counts_what_is_actually_due():
    total = total_line([_fine(), _fine(amount_to_pay="500.00")])

    assert total == "к оплате 800 сом"


def test_total_admits_unknown_sums():
    """«к оплате 300» при неизвестной части сумм было бы неправдой."""
    total = total_line([_fine(), _fine(amount=None, amount_to_pay=None)])

    assert "не менее" in total


# --- страницы ---------------------------------------------------------------


def test_six_fines_fit_one_page_without_arrows():
    assert paginate(6) == [(0, 6)]


def test_seventh_fine_starts_pagination():
    """Первая страница — 5 штрафов и «далее», вторая — «назад» и остаток."""
    assert paginate(7) == [(0, 5), (5, 7)]


def test_middle_page_loses_a_cell_to_each_arrow():
    assert paginate(12) == [(0, 5), (5, 9), (9, 12)]


def test_page_slice_clamps_out_of_range_page():
    fines = [_fine(id=i) for i in range(7)]

    shown, index, pages = page_slice(fines, 99)

    assert index == pages - 1
    assert len(shown) == 2


def test_header_counts_pages_only_when_there_are_several():
    fines = [_fine(id=i) for i in range(3)]

    assert "страница" not in list_header("Штрафы", fines, 0, 1)
    assert "страница 1/2" in list_header("Штрафы", fines, 0, 2)


def test_moment_is_shown_in_local_time():
    """API отдаёт UTC: без перевода нарушение в 14:47 выглядело бы как 08:47."""
    from app.fines_view import moment

    assert moment("2026-08-30T08:47:52.900000+00:00") == "30.08.2026 14:47"


def test_moment_without_zone_is_left_as_is():
    from app.fines_view import moment

    assert moment("2026-08-30T14:47:00") == "30.08.2026 14:47"


def test_total_ignores_paid_fines():
    """На экране «все штрафы» оплаченные завысили бы сумму к оплате."""
    total = total_line([_fine(), _fine(status="paid", amount_to_pay="900.00")])

    assert total == "к оплате 300 сом"


def test_total_is_empty_when_everything_is_paid():
    assert total_line([_fine(status="paid")]) == ""


def test_unknown_sums_are_counted_among_unpaid_only():
    """Оплаченный без суммы не должен превращать итог в «не менее»."""
    total = total_line([_fine(), _fine(status="paid", amount=None, amount_to_pay=None)])

    assert total == "к оплате 300 сом"
