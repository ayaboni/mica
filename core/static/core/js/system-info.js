/* Fills the "In this browser" rows of the System tab. Reads only; nothing is sent anywhere. */
(function () {
  var box = document.getElementById('sysinfoClient');
  if (!box) return;

  function set(key, text) {
    var el = box.querySelector('[data-sys="' + key + '"]');
    if (el) el.textContent = text;
  }

  function fill() {
    var s = window.screen || {};
    var dpr = window.devicePixelRatio || 1;
    set('screen', s.width ? s.width + ' × ' + s.height + (dpr !== 1 ? ' @' + dpr + 'x' : '') : '—');
    set('window', window.innerWidth + ' × ' + window.innerHeight);
    set('language', navigator.language || '—');
    try { set('timezone', Intl.DateTimeFormat().resolvedOptions().timeZone || '—'); }
    catch (e) { set('timezone', '—'); }
    set('online', navigator.onLine ? 'Online' : 'Offline');
    set('cookies', navigator.cookieEnabled ? 'Enabled' : 'Disabled');
  }

  fill();
  window.addEventListener('resize', fill);
  window.addEventListener('online', fill);
  window.addEventListener('offline', fill);
})();
