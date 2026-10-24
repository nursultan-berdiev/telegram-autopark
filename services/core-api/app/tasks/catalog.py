"""Каталог фоновых задач, которые разрешено ставить в расписание.

Свободный ввод имени задачи означал бы запуск произвольной точки входа из формы
в браузере, поэтому имя обязано быть из этого списка. Каталог живёт здесь, а не
в роутере, чтобы им пользовались и старая Jinja-админка, и JSON-роутер веб-SPA
(иначе два списка молча разошлись бы).
"""
from __future__ import annotations

KNOWN_TASKS: dict[str, str] = {
    "app.tasks.fines_tolom.check_fines_tolom": "Проверка штрафов по парку (tolom.kg, с суммами)",
    "app.tasks.fines.check_fines": "Проверка штрафов по парку (carcheck.gov.kg, запасной)",
    "app.tasks.payments_block.block_overdue": "Блокировка двигателя неоплативших к сроку",
    "app.tasks.payments_unblock.unblock_paid": "Разблокировка двигателя после оплаты",
    "app.tasks.fines_enforcement.enforce_fines": "Блокировка двигателя при превышении лимита штрафов",
    "app.tasks.notifications.notify_daily_reminders": "Утреннее напоминание о платеже водителям",
    "app.tasks.notifications.notify_overdue_warning": "Предупреждение водителю за 15 мин до срока",
    "app.tasks.notifications.notify_block_notice": "Уведомление водителю: блокировка включена (22:00)",
    "app.tasks.notifications.notify_admin_digest": "Админ-дайджест по платежам (22:05)",
    "app.tasks.photo_inspections.notify_photo_inspections": "Фотоконтроль: напоминания водителям и алерт о просрочке",
    "app.tasks.ping.ping": "Пробник (проверка связки beat → воркер)",
}
