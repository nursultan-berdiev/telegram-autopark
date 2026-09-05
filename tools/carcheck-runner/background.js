// Оркестратор прогона: берёт номера из core-api, гоняет их через вкладку
// carcheck по одному и заливает найденное обратно.

import { mapViolations } from './mapping.js';

const DEFAULTS = { apiUrl: '', token: '', adminId: '', pauseMs: 7000, dryRun: true };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
}

async function api(path, options = {}) {
  const s = await settings();
  if (!s.apiUrl || !s.token || !s.adminId) throw new Error('не заполнены настройки расширения');
  // Токен здесь узкий (FINES_IMPORT_TOKEN, только /fines/import): расширение —
  // наименее доверенная поверхность, мастер-ключ ему давать нельзя.
  const res = await fetch(s.apiUrl.replace(/\/$/, '') + path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${s.token}`,
      'X-Tg-User-Id': String(s.adminId),
      ...(options.headers || {}),
    },
  });
  if (!res.ok) throw new Error(`core-api ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

async function waitReady(tabId, timeoutMs = 45000) {
  // Форма появляется после гидратации, а она на медленной сети занимает
  // сколько угодно: фиксированная пауза либо коротка, либо тратит время зря.
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await chrome.tabs.sendMessage(tabId, { type: 'ping' });
      if (res && res.ready) return true;
    } catch (_) {
      /* content-скрипт ещё не поднялся */
    }
    await sleep(1000);
  }
  return false;
}

async function findCarcheckTab() {
  const [tab] = await chrome.tabs.query({ url: 'https://carcheck.gov.kg/*' });
  if (tab) return tab;
  return chrome.tabs.create({ url: 'https://carcheck.gov.kg/ru', active: true });
}

async function run(report) {
  const s = await settings();
  const plates = (await api('/fines/import/plates')).filter(Boolean);
  report({ type: 'start', total: plates.length });

  const tab = await findCarcheckTab();
  if (!(await waitReady(tab.id))) {
    report({ type: 'stopped', done: 0, error: 'страница carcheck не загрузилась' });
    return;
  }

  const found = [];
  const raw = [];
  let done = 0;
  let stoppedAt = null;

  for (const plate of plates) {
    let res;
    try {
      res = await chrome.tabs.sendMessage(tab.id, { type: 'check-plate', plate });
    } catch (e) {
      res = { ok: false, error: `вкладка недоступна: ${e.message || e}` };
    }
    done += 1;
    if (!res.ok) {
      // Отказ сервиса — это ответ, а не препятствие: останавливаемся. Но то,
      // что уже собрано по предыдущим номерам, всё равно доводим до импорта.
      stoppedAt = { plate, done, error: res.error, code: res.code };
      break;
    }
    raw.push({ plate, payload: res.payload });
    const { mapped, unmapped } = mapViolations(plate, res.payload);
    found.push(...mapped);
    report({ type: 'progress', plate, done, total: plates.length, count: mapped.length, unmapped: unmapped.length });
    if (done < plates.length) await sleep(s.pauseMs + Math.random() * 2000);
  }

  const finishedAt = new Date().toISOString();
  await chrome.storage.local.set(
    stoppedAt ? { lastRaw: raw, lastStoppedAt: finishedAt } : { lastRaw: raw, lastRunAt: finishedAt }
  );

  let imported = null;
  if (!s.dryRun && found.length) {
    imported = await api('/fines/import', { method: 'POST', body: JSON.stringify(found) });
  } else if (!s.dryRun) {
    imported = { created: 0, skipped: 0, unknown_plates: [], ambiguous_plates: [] };
  }
  if (stoppedAt) report({ type: 'stopped', ...stoppedAt, imported });
  else report({ type: 'done', dryRun: s.dryRun, found: found.length, imported });
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === 'run') {
    const events = [];
    run((e) => {
      events.push(e);
      chrome.runtime.sendMessage({ type: 'event', event: e }).catch(() => {});
    })
      .then(() => sendResponse({ ok: true, events }))
      .catch((e) => sendResponse({ ok: false, error: String(e.message || e) }));
    return true;
  }
  return undefined;
});
