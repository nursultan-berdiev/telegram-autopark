// Общий реалистичный набор данных для всех 4 превью дашборда. Данные тех же
// категорий, что на рабочем дашборде (машины, активные водители, открытые
// алерты) + естественные соседние панели админки (платежи, ТО). Намеренно
// одинаковы во всех вариантах — владелец сравнивает ДИЗАЙН, а не содержимое.
window.FLEET = {
  updated: "сегодня, 14:32",
  stats: {
    cars: 14, free: 5, occupied: 9,
    drivers: 12, online: 9,
    alerts: 5, overdue: 3, maintenance: 2,
    collectedToday: "38 400", collectedMonth: "612 900",
  },
  cars: [
    { plate: "01KG728AAZ", model: "Hyundai Sonata", status: "occupied", driver: "Азамат Беков",        online: true,  speed: 46, engine: "ok",      lat: 42.876, lon: 74.603 },
    { plate: "01KG402CCB", model: "Hyundai Sonata", status: "occupied", driver: "Бекзат Асанов",       online: true,  speed: 0,  engine: "ok",      lat: 42.845, lon: 74.612 },
    { plate: "01KG193BKA", model: "Toyota Corolla", status: "occupied", driver: "Нуржан Карагулов",    online: true,  speed: 31, engine: "ok",      lat: 42.881, lon: 74.585 },
    { plate: "01KG550DEK", model: "Kia K5",         status: "occupied", driver: "Эрлан Токтосунов",    online: true,  speed: 12, engine: "ok",      lat: 42.864, lon: 74.621 },
    { plate: "01KG017FMA", model: "Hyundai Sonata", status: "occupied", driver: "Данияр Исаев",        online: true,  speed: 58, engine: "ok",      lat: 42.858, lon: 74.569 },
    { plate: "01KG884GHЗ", model: "Chevrolet Cobalt", status: "occupied", driver: "Руслан Жээнбеков",  online: true,  speed: 0,  engine: "blocked", lat: 42.872, lon: 74.640 },
    { plate: "01KG233JKL", model: "Toyota Camry",   status: "occupied", driver: "Айбек Мамытов",       online: true,  speed: 24, engine: "ok",      lat: 42.849, lon: 74.597 },
    { plate: "01KG661MNO", model: "Hyundai Sonata", status: "occupied", driver: "Тилек Орозов",        online: true,  speed: 0,  engine: "ok",      lat: 42.890, lon: 74.608 },
    { plate: "01KG305PRS", model: "Kia Rio",        status: "occupied", driver: "Максат Сыдыков",      online: false, speed: 0,  engine: "ok",      lat: 42.830, lon: 74.580 },
    { plate: "01KG742TUV", model: "Hyundai Sonata", status: "free",     driver: null,                  online: true,  speed: 0,  engine: "ok",      lat: 42.868, lon: 74.600 },
    { plate: "01KG128WXY", model: "Toyota Corolla", status: "free",     driver: null,                  online: true,  speed: 0,  engine: "ok",      lat: 42.855, lon: 74.615 },
    { plate: "01KG999ZAB", model: "Chevrolet Cobalt", status: "free",   driver: null,                  online: false, speed: 0,  engine: "ok",      lat: 42.862, lon: 74.592 },
    { plate: "01KG071CDE", model: "Hyundai Sonata", status: "free",     driver: null,                  online: true,  speed: 0,  engine: "ok",      lat: 42.879, lon: 74.631 },
    { plate: "01KG486FGH", model: "Kia K5",         status: "free",     driver: null,                  online: true,  speed: 0,  engine: "ok",      lat: 42.851, lon: 74.576 },
  ],
  alerts: [
    { sev: "critical", type: "payment_overdue",     text: "Бекзат Асанов — просрочка 3 дня, долг 4 500 сом", car: "01KG402CCB", ago: "12 мин" },
    { sev: "warning",  type: "command_unconfirmed", text: "engine_resume ушёл, но блокировка не подтверждена телеметрией — возможно, нет реле", car: "01KG884GHЗ", ago: "38 мин" },
    { sev: "warning",  type: "fines_threshold",     text: "5 неоплаченных штрафов — близко к автоблокировке", car: "01KG728AAZ", ago: "1 ч" },
    { sev: "info",     type: "photo_overdue",       text: "фотоконтроль просрочен на 1 день", car: "01KG193BKA", ago: "2 ч" },
    { sev: "info",     type: "odometer_untrusted",  text: "смена трекера — нужна переустановка базы пробега", car: "01KG305PRS", ago: "3 ч" },
  ],
  payments: [
    { driver: "Бекзат Асанов",     plate: "01KG402CCB", amount: "1 500", status: "overdue", note: "просрочка 3 дня" },
    { driver: "Руслан Жээнбеков",  plate: "01KG884GHЗ", amount: "1 800", status: "overdue", note: "просрочка 1 день" },
    { driver: "Максат Сыдыков",    plate: "01KG305PRS", amount: "1 500", status: "overdue", note: "просрочка 1 день" },
    { driver: "Азамат Беков",      plate: "01KG728AAZ", amount: "1 800", status: "due",     note: "срок сегодня 22:00" },
    { driver: "Нуржан Карагулов",  plate: "01KG193BKA", amount: "1 500", status: "due",     note: "срок сегодня 22:00" },
    { driver: "Эрлан Токтосунов",  plate: "01KG550DEK", amount: "2 000", status: "paid",    note: "оплачено 09:14" },
    { driver: "Данияр Исаев",      plate: "01KG017FMA", amount: "1 800", status: "paid",    note: "оплачено 08:40" },
  ],
  maintenance: [
    { plate: "01KG728AAZ", type: "Замена масла", overKm: 320 },
    { plate: "01KG233JKL", type: "Замена масла", overKm: 80 },
  ],
};
