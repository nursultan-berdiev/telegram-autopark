const log = document.getElementById('log');
const runBtn = document.getElementById('run');

const line = (text, cls) => {
  const div = document.createElement('div');
  if (cls) div.className = cls;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
};

chrome.storage.local.get(['dryRun']).then(({ dryRun }) => {
  document.getElementById('mode').textContent = dryRun === false ? '' : '— пробный прогон';
});

document.getElementById('opts').addEventListener('click', (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.type !== 'event') return;
  const e = msg.event;
  if (e.type === 'start') line(`Машин в парке: ${e.total}`);
  if (e.type === 'progress') {
    const extra = e.unmapped ? `, не разобрано ${e.unmapped}` : '';
    line(`${e.done}/${e.total} ${e.plate}: штрафов ${e.count}${extra}`);
  }
  if (e.type === 'stopped') {
    const where = e.plate ? ` на ${e.plate}` : '';
    line(`Остановились${where}: ${e.error}${e.code ? ` (${e.code})` : ''}`, 'err');
    line('Сырые ответы сохранены — можно посмотреть в настройках.', 'muted');
    showImported(e.imported);
  }
  if (e.type === 'done') {
    if (e.dryRun) {
      line(`Пробный прогон закончен, найдено штрафов: ${e.found}. В core-api ничего не отправлено.`);
    }
    showImported(e.imported);
  }
});

function showImported(i) {
  if (!i) return;
  line(`Заведено ${i.created}, уже было ${i.skipped}.`);
  if (i.unknown_plates && i.unknown_plates.length) {
    line(`Не наши номера: ${i.unknown_plates.join(', ')}`, 'muted');
  }
  if (i.ambiguous_plates && i.ambiguous_plates.length) {
    line(`В парке две машины с таким номером, штраф не заведён: ${i.ambiguous_plates.join(', ')}`, 'err');
  }
}

runBtn.addEventListener('click', async () => {
  runBtn.disabled = true;
  log.textContent = '';
  try {
    const res = await chrome.runtime.sendMessage({ type: 'run' });
    if (res && !res.ok) line(res.error, 'err');
  } catch (e) {
    line(String(e.message || e), 'err');
  } finally {
    runBtn.disabled = false;
  }
});
