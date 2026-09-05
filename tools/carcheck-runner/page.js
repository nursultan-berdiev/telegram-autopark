// Работает в контексте самой страницы: content-script не видит её fetch/XHR.
// Ответ сервиса забираем из того же запроса, который сделала страница, —
// разбирать вёрстку не нужно и она может поменяться в любой момент.
(() => {
  const ENDPOINT = '/violation-check/find-by-plate';

  const publish = (url, status, body) => {
    window.postMessage({ source: 'carcheck-runner', url, status, body }, window.location.origin);
  };

  const origFetch = window.fetch;
  window.fetch = function (...args) {
    const promise = origFetch.apply(this, args);
    try {
      // fetch принимает строку, URL и Request — по каждому свой способ достать адрес.
      const target = args[0];
      const url = typeof target === 'string' ? target : String((target && (target.url || target.href)) || '');
      if (url.includes(ENDPOINT)) {
        promise
          .then((res) => res.clone().text().then((body) => publish(url, res.status, body)))
          .catch(() => {});
      }
    } catch (_) {
      /* перехват не должен ломать саму страницу */
    }
    return promise;
  };

  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__carcheckUrl = url;
    return origOpen.call(this, method, url, ...rest);
  };
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener('load', () => {
      if (this.__carcheckUrl && String(this.__carcheckUrl).includes(ENDPOINT)) {
        publish(this.__carcheckUrl, this.status, this.responseText);
      }
    });
    return origSend.apply(this, args);
  };
})();
