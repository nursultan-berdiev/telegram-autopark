"""FSM ввода данных по машине: трекер, штраф, интервал ТО."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class TrackerForm(StatesGroup):
    external_id = State()


class FineForm(StatesGroup):
    amount = State()
    issued_at = State()
    note = State()


class MaintenanceForm(StatesGroup):
    interval = State()
