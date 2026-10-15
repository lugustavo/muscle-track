/* Workout screen: session timer (pause/resume), per-set tracking with an
 * offline-tolerant queue, rest countdown (beep + vibration), records and
 * screen wake lock. Server calls are absolute-state updates, so replaying
 * queued requests is always safe. */
(function () {
  'use strict';

  var root = document.getElementById('workout');
  if (!root) return;

  var csrf = root.dataset.csrf;
  var QUEUE_KEY = 'mt-queue-v1';
  var REST_KEY = 'mt-rest-v1';

  // ---------- helpers ----------
  function pad(n) { return String(n).padStart(2, '0'); }
  function fmtHMS(s) { return pad(Math.floor(s / 3600)) + ':' + pad(Math.floor(s % 3600 / 60)) + ':' + pad(s % 60); }
  function fmtMS(s) { return pad(Math.floor(s / 60)) + ':' + pad(s % 60); }
  function $(sel, ctx) { return (ctx || document).querySelector(sel); }
  function $$(sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); }
  function readInput(input) {
    var v = input.value.trim();
    return v === '' ? null : v;
  }

  // ---------- preferences (kept on this device) ----------
  var PREFS_KEY = 'mt-prefs-v1';
  var prefs = { sound: true, vibrate: true };
  try { Object.assign(prefs, JSON.parse(localStorage.getItem(PREFS_KEY) || '{}')); } catch (e) { /* defaults */ }
  function savePrefs() {
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (e) { /* storage blocked */ }
  }
  var prefSound = $('#prefSound'), prefVibrate = $('#prefVibrate');
  if (prefSound) {
    prefSound.checked = !!prefs.sound;
    prefSound.addEventListener('change', function () { prefs.sound = prefSound.checked; savePrefs(); });
  }
  if (prefVibrate) {
    prefVibrate.checked = !!prefs.vibrate;
    prefVibrate.addEventListener('change', function () { prefs.vibrate = prefVibrate.checked; savePrefs(); });
  }

  // ---------- session timer ----------
  var timerEl = $('#timer');
  var pauseBtn = $('#pauseBtn');
  var elapsedBase = parseInt(root.dataset.elapsed, 10) || 0;
  var paused = root.dataset.paused === 'true';
  var measuredAt = Date.now();
  var staleDeadline = Date.now() + (parseInt(root.dataset.staleIn, 10) || 0) * 1000;

  function currentElapsed() {
    return elapsedBase + (paused ? 0 : Math.floor((Date.now() - measuredAt) / 1000));
  }
  function renderTimer() {
    timerEl.textContent = fmtHMS(currentElapsed());
    timerEl.classList.toggle('text-muted', paused);
    pauseBtn.innerHTML = paused
      ? '<i class="bi bi-play-fill"></i> Retomar'
      : '<i class="bi bi-pause-fill"></i> Pausar';
    pauseBtn.classList.toggle('btn-warning', paused);
    pauseBtn.classList.toggle('btn-outline-secondary', !paused);
  }
  setInterval(function () {
    renderTimer();
    // Same limit the server enforces: after it the session is closed automatically.
    if (Date.now() >= staleDeadline) {
      window.location.href = root.dataset.myWorkoutsUrl;
    }
  }, 500);
  renderTimer();

  pauseBtn.addEventListener('click', function () {
    var url = paused ? root.dataset.resumeUrl : root.dataset.pauseUrl;
    fetch(url, { method: 'POST', headers: { 'X-CSRFToken': csrf } })
      .then(function (r) { if (!r.ok) throw new Error('fail'); return r.json(); })
      .then(function (d) {
        elapsedBase = d.elapsed_seconds;
        measuredAt = Date.now();
        paused = d.paused;
        renderTimer();
      })
      .catch(function () { alert('Sem conexão: não foi possível pausar agora.'); });
  });

  // ---------- offline-tolerant queue ----------
  var queue = [];
  try { queue = JSON.parse(localStorage.getItem(QUEUE_KEY) || '[]'); } catch (e) { queue = []; }
  queue.forEach(function (item) { item.inflight = false; });
  var flushing = false;
  var syncEl = $('#syncState');

  function saveQueue() {
    try { localStorage.setItem(QUEUE_KEY, JSON.stringify(queue)); } catch (e) { /* storage full/blocked */ }
  }
  function renderSync() {
    var pending = queue.length;
    if (!pending && !flushing) {
      syncEl.className = 'badge bg-success-subtle text-success-emphasis';
      syncEl.innerHTML = '<i class="bi bi-cloud-check"></i> Salvo';
    } else if (!navigator.onLine) {
      syncEl.className = 'badge bg-warning text-dark';
      syncEl.innerHTML = '<i class="bi bi-cloud-slash"></i> Offline · ' + pending + ' pendente(s)';
    } else {
      syncEl.className = 'badge bg-info text-dark';
      syncEl.innerHTML = '<i class="bi bi-cloud-arrow-up"></i> Salvando…';
    }
  }

  function onRejected(status) {
    if (status === 404 || status === 403) {
      alert('Esta sessão de treino já foi encerrada ou expirou. Vou recarregar a página.');
      window.location.href = root.dataset.myWorkoutsUrl;
    } else {
      alert('Um valor não foi aceito pelo servidor. Vou recarregar a página para sincronizar.');
      window.location.reload();
    }
  }

  function flush() {
    if (flushing) return Promise.resolve(queue.length === 0);
    flushing = true;
    renderSync();

    function step() {
      if (!queue.length) return Promise.resolve(true);
      var item = queue[0];
      item.inflight = true;
      return fetch(item.url, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' },
        body: JSON.stringify(item.body)
      }).then(function (resp) {
        item.inflight = false;
        if (resp.ok) {
          queue.shift(); saveQueue();
          return step();
        }
        if (resp.status >= 500 || resp.status === 429) return false; // try again later
        queue.shift(); saveQueue();
        onRejected(resp.status);
        return false;
      }, function () {
        item.inflight = false; // network error: keep it queued
        return false;
      });
    }

    return step().then(function (ok) {
      flushing = false; renderSync();
      return ok;
    });
  }

  function enqueue(url, body) {
    // merge into a not-yet-sent request for the same target (later values win)
    var pending = queue.filter(function (i) { return i.url === url && !i.inflight; })[0];
    if (pending) Object.assign(pending.body, body);
    else queue.push({ url: url, body: body, inflight: false });
    saveQueue();
    renderSync();
    flush();
  }

  window.addEventListener('online', flush);
  window.addEventListener('offline', renderSync);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible' && queue.length) flush();
  });
  setInterval(function () { if (queue.length) flush(); }, 10000);
  window.addEventListener('beforeunload', function (e) {
    if (queue.length) { e.preventDefault(); e.returnValue = ''; }
  });
  renderSync();
  if (queue.length) flush();

  // ---------- rest countdown ----------
  var restBar = $('#restBar');
  var restTime = $('#restTime');
  var restProgress = $('#restProgress');
  var restEnd = 0, restTotal = 0, restInterval = null;
  var audioCtx = null;

  function ensureAudio() {
    if (!audioCtx && (window.AudioContext || window.webkitAudioContext)) {
      try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { audioCtx = null; }
    }
    if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
  }
  function beep() {
    if (!audioCtx || !prefs.sound) return;
    [0, 0.25, 0.5].forEach(function (offset) {
      var osc = audioCtx.createOscillator();
      var gain = audioCtx.createGain();
      osc.frequency.value = 880;
      gain.gain.value = 0.2;
      osc.connect(gain); gain.connect(audioCtx.destination);
      osc.start(audioCtx.currentTime + offset);
      osc.stop(audioCtx.currentTime + offset + 0.15);
    });
  }
  function hideRest() {
    clearInterval(restInterval); restInterval = null; restEnd = 0;
    restBar.classList.add('d-none');
    document.body.style.paddingBottom = '';
    try { sessionStorage.removeItem(REST_KEY); } catch (e) { /* ignore */ }
  }
  function tickRest() {
    var left = Math.ceil((restEnd - Date.now()) / 1000);
    if (left <= 0) {
      hideRest();
      beep();
      if (prefs.vibrate && navigator.vibrate) navigator.vibrate([250, 120, 250]);
      return;
    }
    restTime.textContent = fmtMS(left);
    restProgress.style.width = Math.max(0, Math.min(100, 100 * (1 - left / restTotal))) + '%';
  }
  function startRest(seconds, endAt) {
    if (!seconds || seconds < 1) return;
    restTotal = seconds;
    restEnd = endAt || (Date.now() + seconds * 1000);
    try { sessionStorage.setItem(REST_KEY, JSON.stringify({ end: restEnd, total: restTotal })); } catch (e) { /* ignore */ }
    restBar.classList.remove('d-none');
    document.body.style.paddingBottom = restBar.offsetHeight + 16 + 'px';
    clearInterval(restInterval);
    restInterval = setInterval(tickRest, 250);
    tickRest();
  }
  function adjustRest(deltaSeconds) {
    if (!restEnd) return;
    restEnd += deltaSeconds * 1000;
    restTotal = Math.max(1, restTotal + deltaSeconds);
    try { sessionStorage.setItem(REST_KEY, JSON.stringify({ end: restEnd, total: restTotal })); } catch (e) { /* ignore */ }
    tickRest();
  }
  try {
    var savedRest = JSON.parse(sessionStorage.getItem(REST_KEY) || 'null');
    if (savedRest && savedRest.end > Date.now()) startRest(savedRest.total, savedRest.end);
  } catch (e) { /* ignore */ }

  $('#restSkip').addEventListener('click', hideRest);
  $('#restMinus').addEventListener('click', function () { adjustRest(-15); });
  $('#restPlus').addEventListener('click', function () { adjustRest(15); });

  // ---------- sets ----------
  function toNumber(value) {
    var n = parseFloat(String(value).replace(',', '.'));
    return isNaN(n) ? null : n;
  }

  function refreshRow(row) {
    var done = row.dataset.done === 'true';
    row.classList.toggle('set-done-row', done);
    var btn = $('.set-done', row);
    btn.classList.toggle('btn-success', done);
    btn.classList.toggle('btn-outline-secondary', !done);
    btn.setAttribute('aria-pressed', done ? 'true' : 'false');
    var card = row.closest('[data-exercise-card]');
    var record = toNumber(card.dataset.record || '');
    var weight = toNumber($('.set-weight', row).value);
    var isRecord = done && record !== null && weight !== null && weight > record;
    $('.set-record', row).classList.toggle('d-none', !isRecord);
    return isRecord;
  }

  function refreshCard(card, userAction) {
    var rows = $$('.set-row', card);
    var doneCount = rows.filter(function (r) { return r.dataset.done === 'true'; }).length;
    var complete = rows.length > 0 && doneCount === rows.length;
    var wasComplete = card.dataset.complete === 'true';
    $('[data-progress]', card).textContent = doneCount + '/' + rows.length;
    card.classList.toggle('border-success', complete);
    $('.complete-badge', card).classList.toggle('d-none', !complete);
    card.dataset.complete = complete ? 'true' : 'false';
    if (userAction && complete && !wasComplete) $('.exercise-body', card).classList.add('d-none');
    if (userAction && !complete && wasComplete) $('.exercise-body', card).classList.remove('d-none');
    updateFinishHint();
    updateProgress();
    if (userAction && complete !== wasComplete) reorderQueue(true, card, complete);
  }

  // ---------- focus mode: queue order, current exercise and progress ----------
  var queueEl = $('#queue');
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function cards() { return $$('[data-exercise-card]', queueEl); }
  function isComplete(card) { return card.dataset.complete === 'true'; }

  function updateProgress() {
    var all = cards();
    var doneExercises = all.filter(isComplete).length;
    var rows = $$('.set-row');
    var doneSets = rows.filter(function (r) { return r.dataset.done === 'true'; }).length;
    var label = $('#progressExercises');
    if (label) {
      label.innerHTML = all.length && doneExercises === all.length
        ? 'Todos os exercícios concluídos'
        : 'Exercício <strong id="progressCurrent">' + Math.min(doneExercises + 1, all.length) + '</strong> de ' + all.length;
    }
    var setsEl = $('#progressSetsDone');
    if (setsEl) setsEl.textContent = doneSets;
    var pct = rows.length ? Math.round(100 * doneSets / rows.length) : 0;
    var bar = $('#progressBar');
    if (bar) { bar.style.width = pct + '%'; bar.parentNode.setAttribute('aria-valuenow', pct); }
    var firstPending = all.filter(function (c) { return !isComplete(c); })[0];
    all.forEach(function (c) { c.classList.toggle('is-current', c === firstPending); });
  }

  function placeDivider() {
    var divider = $('#doneDivider');
    var firstDone = cards().filter(isComplete)[0];
    if (!firstDone) { if (divider) divider.remove(); return; }
    if (!divider) {
      divider = document.createElement('div');
      divider.id = 'doneDivider';
      divider.className = 'done-divider';
      divider.innerHTML = '<span>Concluídos</span>';
    }
    queueEl.insertBefore(divider, firstDone);
  }

  // Pending exercises first, then the finished ones; the trainer's order inside each group.
  function reorderQueue(animate, changed, becameComplete) {
    var all = cards();
    var sorted = all.slice().sort(function (a, b) {
      var ca = isComplete(a) ? 1 : 0, cb = isComplete(b) ? 1 : 0;
      return ca - cb || (parseInt(a.dataset.order, 10) - parseInt(b.dataset.order, 10));
    });
    var tops = {};
    if (animate && !reduceMotion) all.forEach(function (c, i) { tops[i] = c.getBoundingClientRect().top; });
    var before = all.slice();
    sorted.forEach(function (c) { queueEl.appendChild(c); });
    placeDivider();
    if (animate && !reduceMotion) {
      sorted.forEach(function (c) {
        var old = tops[before.indexOf(c)];
        var delta = old - c.getBoundingClientRect().top;
        if (!delta) return;
        c.style.transition = 'none';
        c.style.transform = 'translateY(' + delta + 'px)';
        c.getBoundingClientRect(); // flush
        c.style.transition = 'transform .35s ease';
        c.style.transform = '';
        setTimeout(function () { c.style.transition = ''; }, 400);
      });
    }
    updateProgress();
    if (!changed) return;
    var next = sorted.filter(function (c) { return !isComplete(c); })[0];
    // scroll only after the cards finished sliding, so the positions are the final ones
    var wait = animate && !reduceMotion ? 380 : 0;
    var behavior = reduceMotion ? 'auto' : 'smooth';
    setTimeout(function () {
      if (becameComplete) {
        if (next) {
          $('.exercise-body', next).classList.remove('d-none');
          showToast('Próximo: ' + next.dataset.name);
          next.scrollIntoView({ block: 'start', behavior: behavior });
        } else {
          showToast('Todos os exercícios concluídos!');
          var finish = $('#finishForm');
          if (finish) finish.scrollIntoView({ block: 'center', behavior: behavior });
        }
      } else {
        changed.scrollIntoView({ block: 'center', behavior: behavior });
      }
    }, wait);
  }

  function pendingSets() {
    return $$('.set-row').filter(function (r) { return r.dataset.done !== 'true'; }).length;
  }
  function updateFinishHint() {
    var pending = pendingSets();
    var hint = $('#finishHint');
    hint.textContent = pending === 0
      ? 'Todas as séries feitas. Bom trabalho!'
      : 'Faltam ' + pending + ' série(s).';
    hint.classList.toggle('text-success', pending === 0);
  }

  // - / + next to the weight: one step of 2.5 kg (starts from the last time's weight when empty)
  var WEIGHT_STEP = 2.5;
  function fmtWeight(n) { return String(Math.round(n * 100) / 100); }
  function fireChange(input) { input.dispatchEvent(new Event('change', { bubbles: true })); }
  function stepWeight(row, direction) {
    var input = $('.set-weight', row);
    var current = toNumber(input.value);
    if (current === null) {
      var last = toNumber(row.closest('[data-exercise-card]').dataset.last || '');
      current = last !== null ? last : 0;
      if (last !== null && direction > 0) { input.value = fmtWeight(current); fireChange(input); return; }
    }
    input.value = fmtWeight(Math.max(0, current + direction * WEIGHT_STEP));
    fireChange(input);
  }
  // The next set of the same exercise starts with the weight just used, when it has none yet
  function copyWeightToNext(row) {
    var next = row.nextElementSibling;
    while (next && !next.classList.contains('set-row')) next = next.nextElementSibling;
    if (!next || next.dataset.done === 'true') return;
    var from = $('.set-weight', row), to = $('.set-weight', next);
    if (to.value.trim() === '' && from.value.trim() !== '') { to.value = from.value; fireChange(to); }
  }

  function sendSet(row, extra) {
    var body = {
      weight: readInput($('.set-weight', row)),
      reps: readInput($('.set-reps', row))
    };
    Object.assign(body, extra || {});
    enqueue(row.dataset.url, body);
  }

  root.addEventListener('click', function (e) {
    ensureAudio();

    var stepBtn = e.target.closest('.step-btn');
    if (stepBtn) {
      stepWeight(stepBtn.closest('.set-row'), parseInt(stepBtn.dataset.step, 10));
      return;
    }

    var doneBtn = e.target.closest('.set-done');
    if (doneBtn) {
      var row = doneBtn.closest('.set-row');
      var card = row.closest('[data-exercise-card]');
      var nowDone = row.dataset.done !== 'true';
      row.dataset.done = nowDone ? 'true' : 'false';
      var isRecord = refreshRow(row);
      sendSet(row, { done: nowDone });
      refreshCard(card, true);
      if (nowDone) {
        copyWeightToNext(row);
        if (isRecord) showToast('🏆 Novo recorde de carga!');
        if (pendingSets() > 0) startRest(parseInt(card.dataset.rest, 10));
        else hideRest();
      } else {
        hideRest();
      }
      return;
    }

    var header = e.target.closest('.exercise-header');
    if (header && !e.target.closest('a')) {
      $('.exercise-body', header.closest('[data-exercise-card]')).classList.toggle('d-none');
    }
  });

  root.addEventListener('change', function (e) {
    var input = e.target;
    if (input.matches('.set-weight, .set-reps')) {
      var row = input.closest('.set-row');
      refreshRow(row);
      sendSet(row);
    } else if (input.matches('.exercise-notes')) {
      enqueue(input.dataset.url, { notes: input.value });
    }
  });

  $$('.set-row').forEach(refreshRow);
  $$('[data-exercise-card]').forEach(function (c) { refreshCard(c, false); });
  reorderQueue(false);

  // The "Treino iniciado!" notice goes away by itself
  $$('.alert-dismissible').forEach(function (el) {
    setTimeout(function () { el.classList.remove('show'); setTimeout(function () { el.remove(); }, 200); }, 3000);
  });

  // ---------- finish ----------
  var finishForm = $('#finishForm');
  finishForm.addEventListener('submit', function (e) {
    e.preventDefault();
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    // let the blur's change events enqueue, then make sure everything reached the server
    setTimeout(function () {
      flush().then(function (ok) {
        if (!ok) {
          alert('Ainda há alterações sem sincronizar (sem conexão?). Aguarde a internet voltar e tente finalizar de novo.');
          return;
        }
        var pending = pendingSets();
        if (pending > 0 && !confirm('Faltam ' + pending + ' série(s). Finalizar mesmo assim?')) return;
        finishForm.submit();
      });
    }, 50);
  });

  // ---------- toast ----------
  function showToast(text) {
    var el = $('#toast');
    el.textContent = text;
    el.classList.remove('d-none');
    clearTimeout(showToast.t);
    showToast.t = setTimeout(function () { el.classList.add('d-none'); }, 3500);
  }

  // ---------- keep the screen on while training ----------
  var wakeLock = null;
  function lockScreen() {
    if ('wakeLock' in navigator) {
      navigator.wakeLock.request('screen').then(function (l) { wakeLock = l; }).catch(function () { /* denied */ });
    }
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') lockScreen();
  });
  lockScreen();
})();
