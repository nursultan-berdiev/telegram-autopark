const FIELDS = ['apiUrl', 'token', 'adminId', 'pauseMs'];
const el = (id) => document.getElementById(id);

async function load() {
  const s = await chrome.storage.local.get([...FIELDS, 'dryRun', 'lastRaw', 'lastRunAt', 'lastStoppedAt']);
  for (const f of FIELDS) el(f).value = s[f] ?? (f === 'pauseMs' ? 7000 : '');
  el('dryRun').checked = s.dryRun !== false;
  const when = s.lastRunAt || s.lastStoppedAt;
  el('raw').textContent = s.lastRaw
    ? `${when}\n\n${JSON.stringify(s.lastRaw, null, 2)}`
    : 'прогонов ещё не было';
}

el('save').addEventListener('click', async () => {
  const apiUrl = el('apiUrl').value.trim();
  const values = { dryRun: el('dryRun').checked };
  for (const f of FIELDS) values[f] = el(f).value.trim();
  values.pauseMs = Math.max(3000, Number(values.pauseMs) || 7000);

  if (apiUrl) {
    // Право ходить в core-api просим отдельно: в манифесте его нет, адрес
    // у каждой установки свой.
    let parsed;
    try {
      parsed = new URL(apiUrl);
    } catch (_) {
      el('status').textContent = 'адрес нужен со схемой, например https://autopark.example.kg';
      return;
    }
    // По http токен ушёл бы открытым текстом. Исключение — только петля:
    // трафик до localhost из сети не перехватить.
    const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname);
    if (parsed.protocol !== 'https:' && !loopback) {
      el('status').textContent = 'только https — по http токен уйдёт открытым текстом';
      return;
    }
    const origin = parsed.origin + '/*';
    const granted = await chrome.permissions.request({ origins: [origin] });
    if (!granted) {
      el('status').textContent = 'без доступа к этому адресу расширение писать не сможет';
      return;
    }
  }
  await chrome.storage.local.set(values);
  el('status').textContent = 'сохранено';
  setTimeout(() => (el('status').textContent = ''), 2000);
});

load();
