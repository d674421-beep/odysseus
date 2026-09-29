// Tab presence for server auto-shutdown (routes/auto_shutdown_routes.py).
// Each page load is one tab; the server stops shortly after the last tab
// says goodbye. Does nothing unless the server reports it as enabled.
(function () {
  const URL = '/api/presence';
  const PING_MS = 15000;
  const tab = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now());
  let timer = null;

  function beacon(event) {
    const body = new Blob([JSON.stringify({ tab, event })], { type: 'application/json' });
    if (!navigator.sendBeacon || !navigator.sendBeacon(URL, body)) {
      fetch(URL, { method: 'POST', body, keepalive: true, credentials: 'same-origin' }).catch(() => {});
    }
  }

  async function send(event) {
    try {
      const r = await fetch(URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tab, event }),
        credentials: 'same-origin',
      });
      const data = await r.json();
      if (data && data.enabled === false) stop();
    } catch (_) { /* server down or restarting */ }
  }

  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
    window.removeEventListener('pagehide', onHide);
    document.removeEventListener('freeze', onFreeze);
    document.removeEventListener('resume', onResume);
  }

  function onHide() { beacon('close'); }
  function onFreeze() { beacon('frozen'); }
  function onResume() { send('ping'); }

  window.addEventListener('pagehide', onHide);
  window.addEventListener('pageshow', (e) => { if (e.persisted) send('open'); });
  document.addEventListener('freeze', onFreeze);
  document.addEventListener('resume', onResume);
  timer = setInterval(() => send('ping'), PING_MS);
  send('open');
})();
