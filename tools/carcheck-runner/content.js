// Водит форму carcheck так же, как это делает человек: подставить номер,
// нажать «Проверить штрафы», дождаться ответа. reCAPTCHA не трогаем — токен
// выдаёт сама страница в браузере владельца.

const PLATE_INPUT = "input[name='govPlate']";
const SUBMIT_TEXT = /проверить/i;
const WAIT_RESPONSE_MS = 25000;

let lastResponse = null;

const script = document.createElement('script');
script.src = chrome.runtime.getURL('page.js');
script.onload = () => script.remove();
(document.head || document.documentElement).appendChild(script);

window.addEventListener('message', (event) => {
  if (event.source !== window || event.origin !== window.location.origin) return;
  const data = event.data;
  if (data && data.source === 'carcheck-runner') lastResponse = data;
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function setControlledValue(input, value) {
  // Поле управляется React: обычное input.value он не заметит.
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

function findSubmit() {
  const buttons = Array.from(document.querySelectorAll('button'));
  return buttons.find((b) => SUBMIT_TEXT.test(b.textContent || '') && !b.disabled) || null;
}

async function checkPlate(plate) {
  const input = document.querySelector(PLATE_INPUT);
  if (!input) return { plate, ok: false, error: 'поле номера не найдено — вёрстка сайта изменилась' };

  lastResponse = null;
  input.focus();
  setControlledValue(input, plate);
  await sleep(400);

  const button = findSubmit();
  if (!button) return { plate, ok: false, error: 'кнопка проверки не найдена' };
  button.click();

  const deadline = Date.now() + WAIT_RESPONSE_MS;
  while (Date.now() < deadline) {
    if (lastResponse) break;
    await sleep(250);
  }
  if (!lastResponse) return { plate, ok: false, error: 'сервис не ответил за 25 с' };

  let payload = null;
  try {
    payload = JSON.parse(lastResponse.body);
  } catch (_) {
    return { plate, ok: false, error: 'ответ сервиса не разобран как JSON' };
  }
  if (lastResponse.status !== 200) {
    return {
      plate,
      ok: false,
      status: lastResponse.status,
      code: payload && payload.code,
      error: (payload && payload.message) || `HTTP ${lastResponse.status}`,
    };
  }
  return { plate, ok: true, payload };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === 'check-plate') {
    checkPlate(msg.plate).then(sendResponse);
    return true; // ответ асинхронный
  }
  if (msg && msg.type === 'ping') {
    sendResponse({ ready: Boolean(document.querySelector(PLATE_INPUT)) });
  }
  return undefined;
});
