// node --test tools/carcheck-runner/mapping.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';

import { extractList, mapViolations, normalizeAmount, normalizeDate } from './mapping.js';

test('список нарушений находится и в массиве, и в обёртках', () => {
  assert.equal(extractList([{ a: 1 }]).length, 1);
  assert.equal(extractList({ violations: [{ a: 1 }, { a: 2 }] }).length, 2);
  assert.equal(extractList({ data: { content: [{ a: 1 }] } }).length, 1);
  assert.deepEqual(extractList({ message: 'нет данных' }), []);
  assert.deepEqual(extractList(null), []);
});

test('сумма приводится к точке независимо от разделителей', () => {
  assert.equal(normalizeAmount('3 000,50'), '3000.50');
  assert.equal(normalizeAmount(3000.5), '3000.5');
  assert.equal(normalizeAmount(''), null);
  assert.equal(normalizeAmount(null), null);
});

test('мусор в сумме не уезжает в API как число', () => {
  assert.equal(normalizeAmount('оплачено'), null);
  assert.equal(normalizeAmount('3000 сом'), null);
});

test('формат числа читается по последнему разделителю, а не по наличию точки', () => {
  assert.equal(normalizeAmount('3,000.50'), '3000.50');
  assert.equal(normalizeAmount('3 000,50'), '3000.50');
  // Русскоязычный формат: точка — тысячи, запятая — дробная часть.
  assert.equal(normalizeAmount('3.000,50'), '3000.50');
  assert.equal(normalizeAmount('1.234.567,89'), '1234567.89');
  // Три цифры после разделителя — разряд тысяч, а не дробь.
  assert.equal(normalizeAmount('1.234'), '1234');
  assert.equal(normalizeAmount('3,000'), '3000');
});

test('дата разбирается и в ISO, и в формате 17.08.2026', () => {
  // Полночь 17.08 по Бишкеку — это 16.08 18:00 UTC.
  assert.equal(normalizeDate('17.08.2026'), '2026-08-16T18:00:00.000Z');
  // Явная зона в строке уважается как есть.
  assert.equal(normalizeDate('2026-08-17T10:00:00Z'), '2026-08-17T10:00:00.000Z');
  assert.equal(normalizeDate('позавчера'), null);
  assert.equal(normalizeDate(null), null);
});

test('нарушение без номера постановления не импортируется', () => {
  const { mapped, unmapped } = mapViolations('01KG139API', {
    violations: [{ amount: 3000, articleName: 'превышение' }],
  });

  assert.deepEqual(mapped, []);
  assert.equal(unmapped.length, 1, 'иначе повторный прогон завёл бы дубль');
});

test('полное нарушение превращается в элемент импорта', () => {
  const { mapped } = mapViolations('01KG139API', {
    violations: [
      {
        protocolNumber: 'АМ1234567',
        amount: '3 000,50',
        violationDate: '17.08.2026',
        articleName: 'Превышение скорости',
      },
    ],
  });

  assert.equal(mapped.length, 1);
  assert.equal(mapped[0].plate, '01KG139API');
  assert.equal(mapped[0].external_ref, 'АМ1234567');
  assert.equal(mapped[0].amount, '3000.50');
  assert.equal(mapped[0].issued_at, '2026-08-16T18:00:00.000Z');
  assert.equal(mapped[0].note, 'Превышение скорости');
  assert.equal(mapped[0].currency, 'KGS');
});

test('длинные значения обрезаются под размер колонок', () => {
  const { mapped } = mapViolations('01KG139API', [
    { number: 'X'.repeat(200), description: 'Д'.repeat(900) },
  ]);

  assert.equal(mapped[0].external_ref.length, 64);
  assert.equal(mapped[0].note.length, 500);
});

test('пустой ответ не создаёт ни импорта, ни ошибок', () => {
  assert.deepEqual(mapViolations('01KG139API', { violations: [] }), { mapped: [], unmapped: [] });
});

// Ответ, снятый с сервиса 06.09.2026 по 01KG139API. Штрафов на машине нет,
// поэтому список нарушений пустой — но конверт настоящий.
const REAL_EMPTY = {
  vehicle: {
    success: true,
    message: '',
    data: {
      govPlate: '01KG139API',
      steering: 'левый',
      year: 2019,
      brand: 'HYUNDAI',
      model: 'SONATA',
      color: 'белый',
      carTypeName: 'легковой',
      engineVolume: '1999',
      motorType: 'газ',
      tintingWindow: null,
      ownersPeriod: [{ dateFrom: '2023-09-24', dateTo: '' }],
      arestExists: false,
    },
  },
  violations: { success: true, message: '', data: [] },
};

test('на реальном пустом ответе ничего не находится и ничего не падает', () => {
  assert.deepEqual(extractList(REAL_EMPTY), []);
  assert.deepEqual(mapViolations('01KG139API', REAL_EMPTY), { mapped: [], unmapped: [] });
});

test('нарушение внутри violations.data находится, а не теряется молча', () => {
  const payload = structuredClone(REAL_EMPTY);
  payload.violations.data = [
    { protocolNumber: 'АМ1234567', amount: '3 000,50', violationDate: '17.08.2026',
      articleName: 'Превышение скорости' },
  ];

  const { mapped } = mapViolations('01KG139API', payload);

  assert.equal(mapped.length, 1, 'список лежит на уровень глубже, чем в violations');
  assert.equal(mapped[0].external_ref, 'АМ1234567');
  assert.equal(mapped[0].amount, '3000.50');
});

test('карточка машины не просачивается в нарушения', () => {
  // В vehicle.data лежит свой вложенный список (ownersPeriod) — при обходе
  // дерева он не должен сойти за нарушения.
  const onlyVehicle = structuredClone(REAL_EMPTY);
  delete onlyVehicle.violations;

  assert.deepEqual(extractList(onlyVehicle), []);
  assert.deepEqual(mapViolations('01KG139API', onlyVehicle), { mapped: [], unmapped: [] });
});

test('нарушение без суммы всё равно опознаётся при обходе дерева', () => {
  // Сумму сервис не отдаёт вовсе, поэтому опознаём по номеру постановления.
  const payload = { неизвестный: { конверт: [{ protocolNumber: '02-08-051-01-7-382542' }] } };

  assert.equal(extractList(payload).length, 1);
});

test('вложенный список не теряется рядом с объектом-метаданными', () => {
  const payload = { violations: [{ total: 2 }, [{ protocolNumber: 'АМ1' }]] };

  const refs = extractList(payload).map((r) => r.protocolNumber);
  assert.ok(refs.includes('АМ1'), 'соседний объект не должен прятать вложенный массив');
});

test('список находится и при неизвестном имени контейнера', () => {
  const payload = { some: { unexpected: { nesting: [{ number: 'AM9', sum: 100 }] } } };

  assert.equal(extractList(payload).length, 1);
});

// Ответ с реальными штрафами, снят 06.09.2026 по 01KG137API. Записи бедные:
// ни суммы, ни статьи — только тип, номер постановления и время.
const REAL_WITH_FINES = {
  vehicle: { success: true, message: '', data: { govPlate: '01KG137API', brand: 'HYUNDAI' } },
  violations: {
    success: true,
    message: '',
    data: [
      { violationType: 'AFP', protocolNumber: '02-08-051-01-7-382542', violationDate: '2026-08-10T20:06:04' },
      { violationType: 'AFP', protocolNumber: '02-08-051-01-7-266768', violationDate: '2026-08-09T23:29:49.098' },
      { violationType: 'AFP', protocolNumber: '02-08-051-01-7-393990', violationDate: '2026-08-10T00:16:28' },
      { violationType: 'AFP', protocolNumber: '02-08-051-01-7-395449', violationDate: '2026-08-10T19:53:13' },
      { violationType: 'AFP', protocolNumber: '02-08-051-01-7-425532', violationDate: '2026-08-11T15:20:36' },
      { violationType: 'AFP', protocolNumber: '02-08-051-01-7-440811', violationDate: '2026-08-21T06:45:38' },
    ],
  },
};

test('все шесть реальных штрафов разбираются', () => {
  const { mapped, unmapped } = mapViolations('01KG137API', REAL_WITH_FINES);

  assert.equal(mapped.length, 6);
  assert.equal(unmapped.length, 0);
  assert.equal(mapped[0].external_ref, '02-08-051-01-7-382542');
  assert.equal(mapped[0].note, 'AFP', 'тип нарушения — единственное описание, что даёт сервис');
});

test('сумма отсутствует в ответе и не выдумывается', () => {
  const { mapped } = mapViolations('01KG137API', REAL_WITH_FINES);

  assert.ok(mapped.every((f) => f.amount === null), 'в ответе сервиса суммы нет вовсе');
});

test('время читается как бишкекское, а не по часовому поясу браузера', () => {
  const { mapped } = mapViolations('01KG137API', REAL_WITH_FINES);

  // 2026-08-10T00:16:28 по Бишкеку = 2026-08-09T18:16:28Z. Без явной зоны
  // запись уехала бы на другие сутки у владельца в другом поясе.
  const midnight = mapped.find((f) => f.external_ref.endsWith('393990'));
  assert.equal(midnight.issued_at, '2026-08-09T18:16:28.000Z');
});

test('миллисекунды в отметке времени не ломают разбор', () => {
  const { mapped } = mapViolations('01KG137API', REAL_WITH_FINES);

  const withMs = mapped.find((f) => f.external_ref.endsWith('266768'));
  assert.equal(withMs.issued_at, '2026-08-09T17:29:49.098Z');
});
