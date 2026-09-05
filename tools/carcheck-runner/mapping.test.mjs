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
  assert.equal(normalizeDate('17.08.2026').slice(0, 10), '2026-08-17');
  assert.equal(normalizeDate('2026-08-17T10:00:00Z').slice(0, 10), '2026-08-17');
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
  assert.equal(mapped[0].issued_at.slice(0, 10), '2026-08-17');
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
