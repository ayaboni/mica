/* Daily goal: a small "x/5" badge in the page header, a checklist panel, a celebration
   (confetti + sound) when the daily goal is completed, and the evaluation-form pop-up.
   Points are awarded by the server; this file only displays them. It refreshes after any
   request to a feature endpoint (exercise, mood, chat, guidance) by watching window.fetch. */
(function () {
  'use strict';
  var cfg = window.MICA_DAILY_GOAL;
  if (!cfg) return;

  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var css = '' +
    /* badge lives in the header (never over the sidebar / username) */
    '.dg-badge{position:relative;flex-shrink:0;margin-left:auto;margin-right:14px;display:flex;align-items:center;gap:6px;padding:6px 13px;border:0;border-radius:999px;background:#F0C13F;color:#4A3426;font:800 14px Nunito,sans-serif;box-shadow:0 2px 8px rgba(60,45,20,.22);cursor:pointer;transition:background .3s}' +
    '.dg-badge.dg-floating{position:fixed;top:12px;right:12px;margin:0;z-index:200}' +
    '.dg-badge.dg-full{background:#fff;color:#3F6B3A}' +
    '.dg-badge.dg-pop{animation:dgPop .7s cubic-bezier(.2,.9,.3,1.4)}' +
    '.dg-badge.dg-pop::after{content:"";position:absolute;inset:-3px;border-radius:999px;border:3px solid #fff;animation:dgRing .8s ease-out forwards;pointer-events:none}' +
    '@keyframes dgPop{0%{transform:scale(1)}30%{transform:scale(1.28) rotate(-4deg)}60%{transform:scale(.95) rotate(3deg)}100%{transform:scale(1)}}' +
    '@keyframes dgRing{from{opacity:.95;transform:scale(1)}to{opacity:0;transform:scale(1.55)}}' +
    '.dg-plus{position:absolute;left:50%;top:100%;transform:translateX(-50%);font:900 15px Nunito,sans-serif;color:#fff;text-shadow:0 1px 3px rgba(0,0,0,.45);pointer-events:none;animation:dgPlus 1s ease-out forwards;white-space:nowrap}' +
    '@keyframes dgPlus{from{opacity:1;transform:translate(-50%,0)}to{opacity:0;transform:translate(-50%,22px)}}' +
    '@media (max-width:600px){.dg-badge{padding:5px 10px;font-size:12.5px;margin-right:8px}}' +
    /* panel */
    '.dg-panel{position:fixed;z-index:200;width:min(290px,calc(100vw - 16px));background:#fff;color:#3E3226;border-radius:16px;padding:14px 16px;box-shadow:0 8px 24px rgba(0,0,0,.2);font-family:Nunito,sans-serif}' +
    '.dg-panel{transform-origin:top right;opacity:0;visibility:hidden;pointer-events:none;transform:translateY(-12px) scale(.92);transition:opacity .22s ease,transform .38s cubic-bezier(.2,1.2,.3,1),visibility 0s linear .38s}' +
    '.dg-panel.dg-open{opacity:1;visibility:visible;pointer-events:auto;transform:none;transition:opacity .22s ease,transform .38s cubic-bezier(.2,1.2,.3,1),visibility 0s}' +
    '.dg-panel::before{content:"";position:absolute;top:-6px;right:var(--dg-arrow,22px);width:14px;height:14px;background:inherit;transform:rotate(45deg);border-radius:3px}' +
    '.dg-panel .dg-task,.dg-panel h4,.dg-panel p{opacity:0;transform:translateY(-6px);transition:opacity .25s ease,transform .3s ease}' +
    '.dg-panel.dg-open .dg-task,.dg-panel.dg-open h4,.dg-panel.dg-open p{opacity:1;transform:none;transition-delay:calc(70ms + var(--i,0) * 45ms)}' +
    '.dg-panel:not(.dg-open) .dg-bar i{width:0 !important;transition:none}' +
    '.dg-badge .dg-caret{display:inline-block;font-size:10px;transition:transform .3s cubic-bezier(.2,1.2,.3,1)}' +
    '.dg-badge[aria-expanded="true"] .dg-caret{transform:rotate(180deg)}' +
    '.dg-panel h4{margin:0 0 4px;font-size:16px}.dg-panel p{margin:0 0 10px;font-size:12.5px;color:#6B5F4E}' +
    '.dg-bar{height:8px;border-radius:99px;background:#EDE6D3;overflow:hidden;margin-bottom:10px}.dg-bar i{display:block;height:100%;background:#8EAE88;transition:width .5s}' +
    '.dg-task{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:13.5px}.dg-task.dg-done{color:#6B5F4E;text-decoration:line-through}' +
    '.dg-task b{display:inline-grid;place-items:center;width:18px;height:18px;border-radius:50%;border:2px solid #C9C1AC;font-size:11px;line-height:1}.dg-task.dg-done b{background:#8EAE88;border-color:#8EAE88;color:#fff}' +
    '.dg-task.dg-new b{animation:dgPop .7s cubic-bezier(.2,.9,.3,1.4)}' +
    'body.dark-mode .dg-panel{background:#2B2A27;color:#EDE6D3}body.dark-mode .dg-panel p,body.dark-mode .dg-task.dg-done{color:#B9B1A0}body.dark-mode .dg-bar{background:#44413A}' +
    /* celebration */
    '.dg-confetti{position:fixed;inset:0;width:100%;height:100%;z-index:10001;pointer-events:none}' +
    '.dg-banner{position:fixed;top:18px;left:50%;z-index:10002;transform:translate(-50%,-140%);padding:12px 22px;border-radius:999px;background:#fff;color:#3E3226;font:900 16px Nunito,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.28);white-space:nowrap;max-width:calc(100vw - 24px);text-align:center;transition:transform .5s cubic-bezier(.2,.9,.3,1.3)}' +
    '.dg-banner.dg-show{transform:translate(-50%,0)}' +
    'body.dark-mode .dg-banner{background:#2B2A27;color:#EDE6D3}' +
    /* reward pop-up */
    '.dg-overlay{position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.5);display:grid;place-items:center;padding:16px}' +
    '.dg-modal{position:relative;width:min(380px,100%);background:#F7F4E9;color:#3E3226;border-radius:22px;padding:22px 20px 20px;text-align:center;box-shadow:0 12px 40px rgba(0,0,0,.35);font-family:Nunito,sans-serif}' +
    'body.dark-mode .dg-modal{background:#2B2A27;color:#EDE6D3}' +
    '.dg-stage{position:relative;height:210px;margin:0 auto 6px}' +
    '.dg-stage img{position:absolute;left:50%;bottom:0;height:200px;width:auto;transform:translateX(-50%)}' +
    '.dg-sign{position:absolute;left:50%;bottom:6px;transform:translateX(-50%) rotate(-2deg);width:250px;padding:12px 10px;background:#D9B27C;border:4px solid #7B5B3F;border-radius:10px;color:#3A2A18;font-weight:900;font-size:15px;line-height:1.25;box-shadow:0 4px 0 #5C4229,0 8px 14px rgba(0,0,0,.3);z-index:2;word-wrap:break-word}' +
    '.dg-sign small{display:block;font-weight:700;font-size:12px;margin-top:3px}' +
    '.dg-modal p{margin:8px 0 14px;font-size:14px}' +
    '.dg-link{display:inline-block;padding:11px 20px;border-radius:999px;background:#F0C13F;color:#4A3426;font-weight:900;text-decoration:none}' +
    '.dg-link:hover{background:#F7D368}' +
    '.dg-close{position:absolute;top:8px;right:12px;border:0;background:none;font-size:26px;line-height:1;color:inherit;cursor:pointer}' +
    '@media (prefers-reduced-motion:reduce){.dg-panel,.dg-panel *,.dg-badge .dg-caret{transition:none !important}.dg-badge.dg-pop,.dg-task.dg-new b{animation:none}.dg-badge.dg-pop::after,.dg-plus{display:none}.dg-banner{transition:none}}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

  /* ---------- badge + panel ---------- */
  var badge = document.createElement('button');
  badge.type = 'button'; badge.className = 'dg-badge'; badge.setAttribute('aria-label', 'Daily goal');
  var panel = document.createElement('div'); panel.className = 'dg-panel'; panel.setAttribute('aria-hidden', 'true');
  badge.setAttribute('aria-expanded', 'false');
  document.body.appendChild(panel);

  var header = document.querySelector('.content-header');
  var clock = document.getElementById('liveTime');
  if (header) {
    header.insertBefore(badge, clock && clock.parentNode === header ? clock : null);
  } else {
    badge.classList.add('dg-floating');
    document.body.appendChild(badge);
  }

  function placePanel() {
    var r = badge.getBoundingClientRect();
    var right = Math.max(8, Math.round(window.innerWidth - r.right));
    panel.style.top = Math.round(r.bottom + 10) + 'px';
    panel.style.right = right + 'px';
    // point the little arrow at the middle of the badge
    panel.style.setProperty('--dg-arrow', Math.max(12, Math.round(window.innerWidth - right - (r.left + r.width / 2)) - 7) + 'px');
  }
  function isOpen() { return panel.classList.contains('dg-open'); }
  function setOpen(open) {
    if (open) placePanel();
    panel.classList.toggle('dg-open', open);
    panel.setAttribute('aria-hidden', open ? 'false' : 'true');
    badge.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  badge.addEventListener('click', function (e) { e.stopPropagation(); setOpen(!isOpen()); });
  document.addEventListener('click', function (e) { if (isOpen() && !panel.contains(e.target)) setOpen(false); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && isOpen()) setOpen(false); });
  window.addEventListener('resize', function () { if (isOpen()) placePanel(); });

  function esc(t) { var d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; }

  /* ---------- sound: a short synthesized fanfare (no audio file needed) ---------- */
  var ac = null;
  function soundAllowed() {
    try { return localStorage.getItem('notif_sound') !== 'false'; } catch (e) { return true; }
  }
  function playFanfare() {
    if (!soundAllowed()) return;
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!ac) ac = new AC();
      if (ac.state === 'suspended') ac.resume();
      var t0 = ac.currentTime + 0.02;
      function tone(freq, start, dur, type, vol) {
        var o = ac.createOscillator(), g = ac.createGain();
        o.type = type; o.frequency.value = freq;
        g.gain.setValueAtTime(0.0001, t0 + start);
        g.gain.exponentialRampToValueAtTime(vol, t0 + start + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + start + dur);
        o.connect(g); g.connect(ac.destination);
        o.start(t0 + start); o.stop(t0 + start + dur + 0.05);
      }
      // C major arpeggio up, then a held chord
      [523.25, 659.25, 783.99, 1046.5].forEach(function (f, i) { tone(f, i * 0.11, 0.25, 'triangle', 0.22); });
      [523.25, 659.25, 783.99, 1046.5].forEach(function (f) { tone(f, 0.46, 0.9, 'triangle', 0.13); });
      // sparkles
      [2093, 2637, 3136, 2349, 3520].forEach(function (f, i) { tone(f, 0.5 + i * 0.09, 0.18, 'sine', 0.07); });
    } catch (e) { /* sound is a bonus, never an error */ }
  }

  /* ---------- confetti ---------- */
  function launchConfetti() {
    var cv = document.createElement('canvas'); cv.className = 'dg-confetti';
    var dpr = window.devicePixelRatio || 1, W = window.innerWidth, H = window.innerHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    document.body.appendChild(cv);
    var ctx = cv.getContext('2d'); ctx.scale(dpr, dpr);
    var colors = ['#F0C13F', '#8EAE88', '#F3C6CE', '#6FA8DC', '#EE8A5B', '#A99BDB', '#FFFFFF'];
    var parts = [], N = reduceMotion ? 40 : 170;
    function add(x, y, angle, speed) {
      parts.push({
        x: x, y: y, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
        w: 6 + Math.random() * 7, h: 4 + Math.random() * 6, rot: Math.random() * 6.28, vr: (Math.random() - .5) * .35,
        c: colors[(Math.random() * colors.length) | 0], circle: Math.random() < .25, life: 0
      });
    }
    for (var i = 0; i < N; i++) {
      if (i % 3 === 0) add(W * .5, H * .35, -Math.PI / 2 + (Math.random() - .5) * 2.2, 7 + Math.random() * 9);        // centre burst
      else if (i % 3 === 1) add(0, H, -Math.PI / 4 - Math.random() * .6, 11 + Math.random() * 10);                    // left cannon
      else add(W, H, -3 * Math.PI / 4 + Math.random() * .6, 11 + Math.random() * 10);                                  // right cannon
    }
    var start = performance.now(), DURATION = 4200;
    (function frame(now) {
      var el = now - start;
      ctx.clearRect(0, 0, W, H);
      var alive = false;
      parts.forEach(function (p) {
        p.vy += 0.28; p.vx *= 0.992; p.vy *= 0.992;
        p.x += p.vx; p.y += p.vy; p.rot += p.vr; p.life++;
        var fade = el > DURATION - 900 ? Math.max(0, (DURATION - el) / 900) : 1;
        if (p.y < H + 30 && fade > 0) alive = true;
        ctx.save(); ctx.globalAlpha = fade; ctx.translate(p.x, p.y); ctx.rotate(p.rot);
        ctx.fillStyle = p.c;
        if (p.circle) { ctx.beginPath(); ctx.arc(0, 0, p.w / 2, 0, 6.28); ctx.fill(); }
        else ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h * Math.abs(Math.cos(p.life * .12)) + 1);
        ctx.restore();
      });
      if (alive && el < DURATION) requestAnimationFrame(frame); else cv.remove();
    })(start);
  }

  function celebrate(d, afterMs) {
    launchConfetti();
    playFanfare();
    var b = document.createElement('div'); b.className = 'dg-banner'; b.setAttribute('role', 'status');
    b.textContent = '\uD83C\uDF89 Daily quest complete! ' + d.target + '/' + d.target + ' \uD83C\uDF89';
    document.body.appendChild(b);
    requestAnimationFrame(function () { requestAnimationFrame(function () { b.classList.add('dg-show'); }); });
    setTimeout(function () { b.classList.remove('dg-show'); setTimeout(function () { b.remove(); }, 600); }, afterMs || 3800);
  }

  /* ---------- rendering ---------- */
  var lastPoints = null, lastDone = {}, popupShown = false;

  function pointFeedback(gained) {
    badge.classList.remove('dg-pop'); void badge.offsetWidth; badge.classList.add('dg-pop');
    setTimeout(function () { badge.classList.remove('dg-pop'); }, 900);
    var plus = document.createElement('span'); plus.className = 'dg-plus'; plus.textContent = '+' + gained;
    badge.appendChild(plus);
    setTimeout(function () { plus.remove(); }, 1000);
  }

  function render(d) {
    var gained = lastPoints === null ? 0 : d.points - lastPoints;
    var justCompleted = lastPoints !== null && lastPoints < d.target && d.points >= d.target;

    var label = '\u2B50 ' + d.points + '/' + d.target;
    // keep any floating "+1" child; replace only the text node
    if (badge.firstChild && badge.firstChild.nodeType === 3) badge.firstChild.nodeValue = label;
    else badge.insertBefore(document.createTextNode(label), badge.firstChild);
    if (!badge.querySelector('.dg-caret')) {
      var caret = document.createElement('span'); caret.className = 'dg-caret'; caret.textContent = '\u25BE';
      badge.insertBefore(caret, badge.querySelector('.dg-plus'));
    }
    badge.classList.toggle('dg-full', d.points >= d.target);
    badge.title = d.points >= d.target ? 'Daily quest complete!' : 'Daily goal: ' + d.points + ' of ' + d.target + ' points';

    var left = d.days_needed - Math.min(d.days_reached, d.days_needed);
    var sub = d.points >= d.target
      ? 'Daily quest complete! ' + (left > 0 ? left + ' more day' + (left === 1 ? '' : 's') + ' to unlock a surprise.' : '')
      : 'Earn ' + d.target + ' points today (1 per task). Reach it on ' + d.days_needed + ' days for a surprise!';
    panel.innerHTML = '<h4>Daily goal: ' + d.points + '/' + d.target + '</h4><p>' + esc(sub) + '</p>' +
      '<div class="dg-bar"><i style="width:' + Math.round(100 * d.points / d.target) + '%"></i></div>' +
      d.tasks.map(function (t, i) {
        var isNew = t.done && lastPoints !== null && !lastDone[t.key];
        return '<div style="--i:' + (i + 1) + '" class="dg-task' + (t.done ? ' dg-done' : '') + (isNew ? ' dg-new' : '') + '"><b>' + (t.done ? '\u2713' : '') + '</b>' + esc(t.label) + '</div>';
      }).join('');

    lastDone = {}; d.tasks.forEach(function (t) { if (t.done) lastDone[t.key] = true; });
    lastPoints = d.points;

    if (gained > 0) pointFeedback(gained);
    if (justCompleted) celebrate(d);

    if (d.reward_pending && !popupShown) {
      popupShown = true;
      // let the confetti finish before the survey pop-up covers the screen
      setTimeout(function () { showReward(d); }, justCompleted ? 3000 : 0);
    }
  }

  function markSeen() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    fetch(cfg.seenUrl, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': m ? decodeURIComponent(m[1]) : '' } })
      .catch(function () {});
  }

  function showReward(d) {
    var o = document.createElement('div'); o.className = 'dg-overlay';
    o.setAttribute('role', 'dialog'); o.setAttribute('aria-modal', 'true');
    o.innerHTML = '<div class="dg-modal"><button class="dg-close" type="button" aria-label="Close">\u00D7</button>' +
      '<div class="dg-stage"><img src="' + esc(cfg.mascot) + '" alt="Mica">' +
      '<div class="dg-sign">Congratulations, ' + esc(d.name) + '!<small>You hit your daily goal ' + d.days_needed + ' days!</small></div></div>' +
      '<p>Thank you for taking care of yourself with MICA. Would you help us improve by answering our short evaluation form?</p>' +
      '<a class="dg-link" target="_blank" rel="noopener noreferrer" href="' + esc(d.form_url) + '">Open the evaluation form</a></div>';
    function close() { markSeen(); o.remove(); }
    o.querySelector('.dg-close').addEventListener('click', close);
    o.querySelector('.dg-link').addEventListener('click', function () { setTimeout(close, 0); });
    o.addEventListener('click', function (e) { if (e.target === o) close(); });
    document.body.appendChild(o);
  }

  var inflight = false, again = false;
  function refresh() {
    if (inflight) { again = true; return; }
    inflight = true;
    realFetch(cfg.url, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) render(d); })
      .catch(function () {})
      .then(function () { inflight = false; if (again) { again = false; refresh(); } });
  }

  // Refresh after any call to a feature that can earn a point.
  var TRACKED = [/\/api\/exercises\/log\//, /\/api\/breathe\/save\//, /\/api\/mood\/save\//,
                 /\/api\/chatbot\//, /\/api\/guidance\/(send|request)\//];
  var realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    var p = realFetch(input, init);
    if (TRACKED.some(function (re) { return re.test(url); })) {
      p.then(function () { setTimeout(refresh, 400); }, function () {});
    }
    return p;
  };

  refresh();
})();
