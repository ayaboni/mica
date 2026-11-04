/* MicaPoll: one polite polling helper shared by the student and staff dashboards.
 *
 *   MicaPoll.every(fn, {active, hidden, enabled, immediate})  -> {now(), stop()}
 *   MicaPoll.json(url)                                         -> Promise<parsed JSON>
 *
 * - Chained setTimeout (never setInterval): the next request is only scheduled after the last one
 *   finished, so a slow server never gets a pile-up of overlapping requests.
 * - `active` is the delay in ms while the tab is visible; `hidden` is the delay while it is in the
 *   background (null = pause until the tab comes back). Coming back to the tab polls immediately.
 * - Errors back off (x2 per failure, up to 60 s) and reset on the first success.
 * - json() sends the session cookie, throws on non-2xx, and when the server says the session has
 *   expired or the account was suspended (401) it stops every poll and goes to the login page.
 */
(function () {
  'use strict';

  var handles = [];

  function stopAll() {
    handles.slice().forEach(function (h) { h.stop(); });
  }

  function toLogin(reason) {
    stopAll();
    window.location.href = '/login/?' + (reason || 'expired') + '=1';
  }

  function json(url) {
    return fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        if (r.redirected && /\/login\//.test(r.url)) { toLogin('expired'); throw new Error('signed out'); }
        if (r.status === 401) {
          return r.json().catch(function () { return {}; }).then(function (d) {
            if (d && (d.expired || d.suspended)) toLogin(d.suspended ? 'suspended' : 'expired');
            var e = new Error('unauthorized'); e.status = 401; throw e;
          });
        }
        if (!r.ok) { var err = new Error('http ' + r.status); err.status = r.status; throw err; }
        return r.json();
      });
  }

  function every(fn, opts) {
    opts = opts || {};
    var active = opts.active || 10000;
    var hidden = opts.hidden === undefined ? active * 4 : opts.hidden;   // null = pause while hidden
    var enabled = opts.enabled || function () { return true; };
    var fails = 0, timer = null, running = false, dead = false;

    function delay() {
      var d = document.hidden ? hidden : active;
      if (d === null) return null;
      if (fails) d = Math.min(d * Math.pow(2, fails), 60000);
      return d;
    }

    function schedule() {
      if (dead) return;
      clearTimeout(timer);
      var d = delay();
      if (d !== null) timer = setTimeout(tick, d);       // null: paused, visibilitychange restarts us
    }

    function tick() {
      if (dead || running) return;
      if (!enabled() || (document.hidden && hidden === null)) { schedule(); return; }
      running = true;
      var p;
      try { p = Promise.resolve(fn()); } catch (e) { p = Promise.reject(e); }
      p.then(function () { fails = 0; }, function () { fails = Math.min(fails + 1, 5); })
        .then(function () { running = false; schedule(); });
    }

    function onVisible() {
      if (!document.hidden && !dead) { clearTimeout(timer); tick(); }
    }
    document.addEventListener('visibilitychange', onVisible);

    var handle = {
      now: function () { if (!dead) { clearTimeout(timer); tick(); } },
      stop: function () {
        dead = true;
        clearTimeout(timer);
        document.removeEventListener('visibilitychange', onVisible);
        var i = handles.indexOf(handle);
        if (i > -1) handles.splice(i, 1);
      }
    };
    handles.push(handle);
    if (opts.immediate) tick(); else schedule();
    return handle;
  }

  window.MicaPoll = { every: every, json: json, stopAll: stopAll };
})();
