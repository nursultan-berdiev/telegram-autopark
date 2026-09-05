// Разбор ответа carcheck. Вынесено отдельным модулем, потому что форму
// успешного ответа мы вживую не видели и ключи подобраны по кандидатам —
// эту часть придётся править, и она должна быть под тестами.

const LIST_KEYS = ['violations', 'items', 'data', 'content', 'result', 'records'];
const REF_KEYS = ['protocolNumber', 'resolutionNumber', 'decisionNumber', 'docNumber', 'number', 'seriaNumber', 'id'];
const AMOUNT_KEYS = ['amount', 'sum', 'penaltySum', 'fineAmount', 'totalAmount', 'debt'];
const DATE_KEYS = ['violationDate', 'issuedAt', 'protocolDate', 'createdAt', 'date', 'decisionDate'];
const NOTE_KEYS = ['articleName', 'violationName', 'article', 'description', 'place', 'address'];

export const pick = (obj, keys) => {
  for (const k of keys) {
    if (obj && obj[k] !== undefined && obj[k] !== null && obj[k] !== '') return obj[k];
  }
  return null;
};

export function extractList(payload) {
  if (Array.isArray(payload)) return payload;
  for (const k of LIST_KEYS) {
    const value = payload && payload[k];
    if (Array.isArray(value)) return value;
    if (value && Array.isArray(value.content)) return value.content;
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

export function normalizeDate(raw) {
  if (!raw) return null;
  // Сервис может отдать и ISO, и «17.08.2026» — второе Date не понимает.
  const dotted = String(raw).match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
  const value = dotted ? `${dotted[3]}-${dotted[2]}-${dotted[1]}` : String(raw);
  const parsed = new Date(value);
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
