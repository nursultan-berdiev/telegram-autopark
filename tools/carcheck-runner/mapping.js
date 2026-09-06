// Разбор ответа carcheck. Вынесен отдельным модулем, потому что форму ответа
// сервис не документирует и она может измениться без предупреждения.
//
// Фактический ответ (снят 06.09.2026 по 01KG139API):
//   {"vehicle":{"success":true,"message":"","data":{...}},
//    "violations":{"success":true,"message":"","data":[]}}
// То есть список лежит не в `violations`, а на уровень глубже, в
// `violations.data`. Поэтому конверт разворачивается рекурсивно, а не по
// фиксированному пути: угаданный путь молча вернул бы пусто при живых штрафах.

const LIST_KEYS = ['violations', 'items', 'data', 'content', 'result', 'records'];
const REF_KEYS = ['protocolNumber', 'resolutionNumber', 'decisionNumber', 'docNumber', 'number', 'seriaNumber', 'id'];
const AMOUNT_KEYS = ['amount', 'sum', 'penaltySum', 'fineAmount', 'totalAmount', 'debt'];
const DATE_KEYS = ['violationDate', 'issuedAt', 'protocolDate', 'createdAt', 'date', 'decisionDate'];
// Защита от бесконечной рекурсии по произвольному JSON.
const MAX_DEPTH = 8;
const NOTE_KEYS = ['articleName', 'violationName', 'article', 'description', 'place', 'address', 'violationType'];

export const pick = (obj, keys) => {
  for (const k of keys) {
    if (obj && obj[k] !== undefined && obj[k] !== null && obj[k] !== '') return obj[k];
  }
  return null;
};

function looksLikeViolation(rec) {
  if (!rec || typeof rec !== 'object' || Array.isArray(rec)) return false;
  // Только номер постановления: сумму сервис не отдаёт вовсе (см. шапку файла),
  // и требовать её значило бы отбрасывать настоящие нарушения.
  return pick(rec, REF_KEYS) !== null;
}

function objectsIn(arr) {
  return arr.filter((x) => x && typeof x === 'object' && !Array.isArray(x));
}

export function extractList(payload, depth = 0) {
  if (depth > MAX_DEPTH) return [];
  if (Array.isArray(payload)) {
    // Вложенные массивы добираем отдельно: в массиве вперемешку с объектом-
    // метаданными список нарушений иначе потерялся бы молча.
    const nested = payload.flatMap((i) => (Array.isArray(i) ? extractList(i, depth + 1) : []));
    return [...objectsIn(payload), ...nested];
  }
  if (!payload || typeof payload !== 'object') return [];

  // Сервис назвал контейнер понятно — берём всё, что в нём лежит, включая
  // записи без суммы: их надо показать как неразобранные, а не потерять.
  for (const key of LIST_KEYS) {
    if (payload[key] === undefined) continue;
    const found = extractList(payload[key], depth + 1);
    if (found.length) return found;
  }
  // Дальше идёт угадывание по всему дереву, и тут уже нужны доказательства:
  // иначе за нарушения сойдут владельцы, периоды и прочие вложенные списки.
  for (const value of Object.values(payload)) {
    const found = extractList(value, depth + 1).filter(looksLikeViolation);
    if (found.length) return found;
  }
  return [];
}

export function normalizeAmount(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  // Пробелы, включая неразрывный, встречаются как разделитель тысяч.
  const text = String(raw).replace(/[\s ]/g, '');
  if (!/^-?[\d.,]+$/.test(text)) return null;

  // Формат определяем по последнему разделителю, а не по наличию точки:
  // «3.000,50» и «3,000.50» — одна и та же сумма, но по точке читаются наоборот.
  const last = Math.max(text.lastIndexOf('.'), text.lastIndexOf(','));
  if (last === -1) return text;

  const tail = text.slice(last + 1);
  // Ровно три цифры после последнего разделителя — это разряд тысяч («1.234»):
  // дробная часть такой длины в деньгах не встречается.
  const head = text.slice(0, last).replace(/[.,]/g, '');
  const normalized = tail.length === 3 ? head + tail : `${head}.${tail}`;
  return /^-?\d+(\.\d+)?$/.test(normalized) ? normalized : null;
}

// Сервис отдаёт время без зоны («2026-08-10T20:06:04»), и это бишкекское
// местное. Без явной зоны его читал бы часовой пояс браузера: у владельца
// за границей ночное нарушение уехало бы на соседние сутки, а по датам
// считается окно правила «N штрафов».
const SERVICE_TZ = '+06:00';

export function normalizeDate(raw) {
  if (!raw) return null;
  const text = String(raw).trim();
  // Формат «17.08.2026» Date не понимает вовсе.
  const dotted = text.match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
  if (dotted) {
    const parsed = new Date(`${dotted[3]}-${dotted[2]}-${dotted[1]}T00:00:00${SERVICE_TZ}`);
    return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
  }
  const naive = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(text);
  const parsed = new Date(naive ? `${text.replace(' ', 'T')}${SERVICE_TZ}` : text);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

export function mapViolations(plate, payload) {
  const mapped = [];
  const unmapped = [];
  for (const item of extractList(payload)) {
    const ref = pick(item, REF_KEYS);
    if (!ref) {
      // Без номера постановления следующий прогон завёл бы дубль — не берём.
      unmapped.push(item);
      continue;
    }
    mapped.push({
      plate,
      external_ref: String(ref).slice(0, 64),
      amount: normalizeAmount(pick(item, AMOUNT_KEYS)),
      currency: 'KGS',
      issued_at: normalizeDate(pick(item, DATE_KEYS)),
      note: (pick(item, NOTE_KEYS) || '').toString().slice(0, 500) || null,
    });
  }
  return { mapped, unmapped };
}
