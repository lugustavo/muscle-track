/* Workout screen: session timer (pause/resume), per-set tracking with an
 * offline-tolerant queue, rest countdown (beep + vibration), records and
 * screen wake lock. Server calls are absolute-state updates, so replaying
 * queued requests is always safe.
 *
 * Offline: the whole workout (timer, sets, comments) is also kept in this device's storage (static/js/workout_offline.js),
 * so the screen can be reopened with no connection (the service worker keeps the page), a workout can be started or
 * finished offline, and the device sends everything when the connection is back (core/workout_offline.py). */
(function () {
  'use strict';

  // Django's JS catalog (/jsi18n/); identity fallbacks if it has not loaded
  var gettext = window.gettext || function (s) { return s; };
  var interpolate = window.interpolate || function (s, o, named) {
    if (named) { return s.replace(/%\((\w+)\)s/g, function (m, k) { return String(o[k]); }); }
    var i = 0;
    return s.replace(/%s/g, function () { return String(o[i++]); });
  };

  var root = document.getElementById('workout');
  if (!root) return;

  var MT = window.MTWorkout;
  var csrf = (MT && MT.csrf()) || root.dataset.csrf;   // the cookie first: a kept copy of this page may carry an old token
  var QUEUE_KEY = 'mt-queue-v1';
  var REST_KEY = 'mt-rest-v1';
  var routineId = root.dataset.routine;
  var sessionId = root.dataset.session || null;
  var isDraft = root.dataset.draft === 'true';        // a workout the server does not know yet (started offline)
  var offlineCopy = root.hasAttribute('data-offline-copy');   // this page came from the service worker's copy

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

  // ---------- the workout kept on this device ----------
  // state: see workout_offline.js. It is the truth for the timer while offline; a page fresh from the server refreshes it.
  var hadState = false;
  var state = MT.active();
  function hasProgress(st) {
    return Object.keys(st.exercises || {}).some(function (prx) {
      return Object.keys(st.exercises[prx].sets || {}).some(function (n) { return st.exercises[prx].sets[n].d; });
    });
  }
  if (state && String(state.routine) !== String(routineId)) {
    // another workout was left on this device: if it never reached the server, do not lose it
    if (state.draft && !state.finished && hasProgress(state)) MT.addPending(MT.payloadFrom(state, false));
    state = null;
  }
  if (state && !isDraft && state.session && String(state.session) !== String(sessionId)) state = null;   // an older session of this routine
  hadState = !!state;
  var pageElapsed = parseInt(root.dataset.elapsed, 10) || 0;
  var pagePausedSeconds = parseInt(root.dataset.pausedSeconds, 10) || 0;
  var pageRendered = parseInt(root.dataset.rendered, 10) || Date.now();
  var pagePaused = root.dataset.paused === 'true';
  if (!state) {
    var reference = offlineCopy ? pageRendered : Date.now();   // a kept page shows the elapsed time of when it was kept
    state = {
      uuid: MT.uuid(), routine: routineId, routineName: root.dataset.routineName || '', session: sessionId, draft: isDraft,
      startedAt: isDraft ? Date.now() : reference - (pageElapsed + pagePausedSeconds) * 1000,
      pausedSeconds: isDraft ? 0 : pagePausedSeconds, pausedAt: !isDraft && pagePaused ? reference : null,
      notes: '', finished: false, exercises: {}, url: root.dataset.executeUrl
    };
  } else if (!offlineCopy && !isDraft) {
    // fresh from the server: it knows the real session and its timer (a draft that was just sent is now this session)
    state.session = sessionId; state.draft = false;
    state.startedAt = Date.now() - (pageElapsed + pagePausedSeconds) * 1000;
    state.pausedSeconds = pagePausedSeconds;
    state.pausedAt = pagePaused ? Date.now() : null;
  }
  state.routineName = root.dataset.routineName || state.routineName;
  state.url = (isDraft ? root.dataset.draftUrl : root.dataset.executeUrl) || state.url;   // where the offline page sends the student back to
  var times = {};   // when each set was marked done (device time), kept in the state

  // ---------- session timer ----------
  var timerEl = $('#timer');
  var pauseBtn = $('#pauseBtn');

  function currentElapsed() {
    var now = Date.now();
    var seconds = (now - state.startedAt) / 1000 - (state.pausedSeconds || 0) - (state.pausedAt ? (now - state.pausedAt) / 1000 : 0);
    return Math.max(0, Math.floor(seconds));
  }
  function isPaused() { return !!state.pausedAt; }
  function renderTimer() {
    var paused = isPaused();
    timerEl.textContent = fmtHMS(currentElapsed());
    timerEl.classList.toggle('text-muted', paused);
    var label = paused ? gettext('Retomar') : gettext('Pausar');
    pauseBtn.innerHTML = '<i class="bi bi-' + (paused ? 'play' : 'pause') + '-fill"></i><span class="d-none d-sm-inline"> ' + label + '</span>';
    pauseBtn.setAttribute('aria-label', label);
    pauseBtn.classList.toggle('btn-warning', paused);
    pauseBtn.classList.toggle('btn-outline-secondary', !paused);
  }
  // No redirect when the server's idle limit passes: a workout done offline would be thrown away. If the server did close
  // the session meanwhile, the next sync reopens it (core/workout_offline.py).
  setInterval(renderTimer, 500);
  renderTimer();

  function setPauseLocally() {
    var now = Date.now();
    if (state.pausedAt) { state.pausedSeconds = (state.pausedSeconds || 0) + (now - state.pausedAt) / 1000; state.pausedAt = null; }
    else { state.pausedAt = now; }
    state.timerLocal = true;   // the server never heard of this pause: the workout is finished through the full sync
    renderTimer();
    snapshot();
  }

  pauseBtn.addEventListener('click', function () {
    var paused = isPaused();
    var url = paused ? root.dataset.resumeUrl : root.dataset.pauseUrl;
    if (!url || navigator.onLine === false) { setPauseLocally(); return; }
    fetch(url, { method: 'POST', headers: { 'X-CSRFToken': csrf } })
      .then(function (r) { if (!r.ok) throw new Error('fail'); return r.json(); })
      .then(function (d) {
        var now = Date.now();
        if (paused) { state.pausedSeconds = (state.pausedSeconds || 0) + (now - state.pausedAt) / 1000; state.pausedAt = null; }
        else { state.pausedAt = now; }
        // line the device up with the server's own count
        state.startedAt = now - (d.elapsed_seconds + (state.pausedSeconds || 0)) * 1000;
        renderTimer();
        snapshot();
      })
      .catch(function () { setPauseLocally(); });   // no connection: pause on the device, the server is told later
  });

  // ---------- offline-tolerant queue ----------
  var queue = [];
  try { queue = JSON.parse(localStorage.getItem(QUEUE_KEY) || '[]'); } catch (e) { queue = []; }
  queue.forEach(function (item) { item.inflight = false; });
  var flushing = false;
  var unreachable = false;   // the last try failed with no answer: the device says it is online, the server is not reachable
  var syncEl = $('#syncState');

  function saveQueue() {
    try { localStorage.setItem(QUEUE_KEY, JSON.stringify(queue)); } catch (e) { /* storage full/blocked */ }
  }
  function renderSync() {
    var pending = queue.length;
    if (isDraft) {
      syncEl.className = 'badge bg-info text-dark';
      syncEl.innerHTML = '<i class="bi bi-phone"></i> ' + gettext('Guardado neste aparelho');
    } else if (!pending && !flushing) {
      syncEl.className = 'badge bg-success-subtle text-success-emphasis';
      syncEl.innerHTML = '<i class="bi bi-cloud-check"></i> ' + gettext('Salvo');
    } else if (!navigator.onLine || unreachable) {
      syncEl.className = 'badge bg-warning text-dark';
      syncEl.innerHTML = '<i class="bi bi-cloud-slash"></i> ' + interpolate(gettext('Offline · %s pendente(s)'), [pending]);
    } else {
      syncEl.className = 'badge bg-info text-dark';
      syncEl.innerHTML = '<i class="bi bi-cloud-arrow-up"></i> ' + gettext('Salvando…');
    }
  }

  function onRejected(status) {
    if (status === 404 || status === 403) {
      // the server no longer has this session (closed for being idle, or deleted): send it everything the device kept
      repairFromDevice();
    } else {
      alert(gettext('Um valor não foi aceito pelo servidor. Vou recarregar a página para sincronizar.'));
      window.location.reload();
    }
  }

  var repairing = false;
  function repairFromDevice() {
    if (repairing) return;
    repairing = true;
    snapshot();
    MT.send(MT.payloadFrom(state, false)).then(function (result) {
      repairing = false;
      if (!result.ok) {
        if (result.status === 0) return;   // no connection after all: the queue and the state are still here
        alert(gettext('Esta sessão de treino já foi encerrada ou expirou. Vou recarregar a página.'));
        window.location.href = root.dataset.myWorkoutsUrl;
        return;
      }
      queue.length = 0; saveQueue();
      if (result.data.finished) { MT.clearActive(); window.location.href = result.data.summary_url; return; }
      state.session = String(result.data.session); state.draft = false; MT.saveActive(state);
      if (String(result.data.session) !== String(sessionId)) window.location.replace(result.data.execute_url);
    });
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
        if (resp.redirected) { unreachable = true; return false; }   // sent to the login page: keep it, nothing was saved
        unreachable = false;
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
        unreachable = true;
        return false;
      });
    }

    return step().then(function (ok) {
      flushing = false; renderSync();
      return ok;
    });
  }

  function enqueue(url, body) {
    if (!url) { snapshot(); return; }   // a draft has no session on the server yet: the state is what is kept
    snapshot();
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
        ? gettext('Todos os exercícios concluídos')
        : interpolate(gettext('Exercício <strong id="progressCurrent">%(current)s</strong> de %(total)s'), {
          current: Math.min(doneExercises + 1, all.length), total: all.length
        }, true);
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
      divider.innerHTML = '<span>' + gettext('Concluídos') + '</span>';
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
          showToast(interpolate(gettext('Próximo: %(name)s'), { name: next.dataset.name }, true));
          next.scrollIntoView({ block: 'start', behavior: behavior });
        } else {
          showToast(gettext('Todos os exercícios concluídos!'));
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
      ? gettext('Todas as séries feitas. Bom trabalho!')
      : interpolate(gettext('Faltam %s série(s).'), [pending]);
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
      times[card.dataset.prx + ':' + row.dataset.n] = nowDone ? Date.now() : undefined;
      var isRecord = refreshRow(row);
      sendSet(row, { done: nowDone });
      refreshCard(card, true);
      if (nowDone) {
        copyWeightToNext(row);
        if (isRecord) showToast(gettext('🏆 Novo recorde de carga!'));
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

  // ---------- the device's copy of the workout ----------
  var sessionNotes = $('#sessionNotes');

  // DOM -> state (written to this device's storage on every change)
  function snapshot() {
    state.exercises = {};
    $$('[data-exercise-card]').forEach(function (card) {
      var prx = card.dataset.prx;
      var exercise = { notes: $('.exercise-notes', card).value, sets: {} };
      $$('.set-row', card).forEach(function (row) {
        var done = row.dataset.done === 'true';
        var key = prx + ':' + row.dataset.n;
        exercise.sets[row.dataset.n] = {
          w: readInput($('.set-weight', row)), r: readInput($('.set-reps', row)), d: done,
          t: done ? (times[key] || (times[key] = Date.now())) : undefined
        };
      });
      state.exercises[prx] = exercise;
    });
    state.notes = sessionNotes ? sessionNotes.value : '';
    MT.saveActive(state);
  }

  // state -> DOM (a screen opened offline from the service worker's copy, or a draft reopened)
  function restore() {
    $$('[data-exercise-card]').forEach(function (card) {
      var saved = state.exercises[card.dataset.prx];
      if (!saved) return;
      $$('.set-row', card).forEach(function (row) {
        var set = saved.sets[row.dataset.n];
        if (!set) return;
        $('.set-weight', row).value = set.w === null || set.w === undefined ? '' : set.w;
        $('.set-reps', row).value = set.r === null || set.r === undefined ? '' : set.r;
        row.dataset.done = set.d ? 'true' : 'false';
        if (set.d && set.t) times[card.dataset.prx + ':' + row.dataset.n] = set.t;
      });
      $('.exercise-notes', card).value = saved.notes || '';
    });
    if (sessionNotes) sessionNotes.value = state.notes || '';
  }

  // changes made here that the server has not received yet (the one-set queue), shown on a page fresh from the server
  function applyQueue() {
    queue.forEach(function (item) {
      var row = $$('.set-row').filter(function (r) { return r.dataset.url === item.url; })[0];
      if (row) {
        if ('weight' in item.body) $('.set-weight', row).value = item.body.weight === null ? '' : item.body.weight;
        if ('reps' in item.body) $('.set-reps', row).value = item.body.reps === null ? '' : item.body.reps;
        if ('done' in item.body) row.dataset.done = item.body.done ? 'true' : 'false';
      }
      var note = $$('.exercise-notes').filter(function (t) { return t.dataset.url === item.url; })[0];
      if (note && 'notes' in item.body) note.value = item.body.notes;
    });
  }

  if (hadState && (offlineCopy || isDraft)) restore();
  else applyQueue();

  $$('.set-row').forEach(refreshRow);
  $$('[data-exercise-card]').forEach(function (c) { refreshCard(c, false); });
  reorderQueue(false);
  snapshot();
  if (sessionNotes) sessionNotes.addEventListener('input', function () { clearTimeout(snapshot.t); snapshot.t = setTimeout(snapshot, 300); });
  window.addEventListener('pagehide', snapshot);
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'hidden') snapshot(); });

  // The "Treino iniciado!" notice goes away by itself
  $$('.alert-dismissible').forEach(function (el) {
    setTimeout(function () { el.classList.remove('show'); setTimeout(function () { el.remove(); }, 200); }, 3000);
  });

  // ---------- finish ----------
  var finishForm = $('#finishForm');
  var donePanel = $('#offlineDone');
  var lastTouch = Date.now();
  ['click', 'input', 'keydown', 'touchstart'].forEach(function (name) {
    root.addEventListener(name, function () { lastTouch = Date.now(); }, { passive: true });
  });

  function showDonePanel() {
    $$('#queue, #finishForm, .workout-header, [data-draft-note]').forEach(function (el) { el.classList.add('d-none'); });
    var discard = $('[data-discard-draft]'); if (discard) discard.parentNode.classList.add('d-none');
    var form = root.querySelector('form[action*="discard"]'); if (form) form.classList.add('d-none');
    donePanel.classList.remove('d-none');
    hideRest();
    window.scrollTo(0, 0);
  }

  // Send the finished workout; when it is accepted, go to the summary the server made of it
  function deliver() {
    var button = $('[data-offline-send]', donePanel);
    button.disabled = true;
    MT.flushPending().then(function (delivered) {
      button.disabled = false;
      var mine = delivered.filter(function (d) { return d.payload.uuid === state.uuid; })[0];
      if (mine && mine.data.summary_url) { MT.clearActive(); window.location.href = mine.data.summary_url; }
    });
  }

  function finishLocally() {
    state.finished = true;
    state.endedAt = Date.now();
    if (state.pausedAt) { state.pausedSeconds = (state.pausedSeconds || 0) + (state.endedAt - state.pausedAt) / 1000; state.pausedAt = null; }
    snapshot();
    MT.addPending(MT.payloadFrom(state, true));
    showDonePanel();
    deliver();
  }

  finishForm.addEventListener('submit', function (e) {
    e.preventDefault();
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    // let the blur's change events enqueue, then make sure everything reached the server
    setTimeout(function () {
      var pending = pendingSets();
      if (pending > 0 && !confirm(interpolate(gettext('Faltam %s série(s). Finalizar mesmo assim?'), [pending]))) return;
      // A draft, a pause the server never heard of or a lost connection: the whole workout goes in one full sync.
      // Otherwise the usual way: make sure every set arrived, then end the session on the server.
      if (isDraft || state.timerLocal || navigator.onLine === false) { finishLocally(); return; }
      flush().then(function (ok) {
        if (!ok) { finishLocally(); return; }
        state.finished = true; state.endedAt = Date.now(); snapshot();   // until the summary page confirms it
        finishForm.submit();
      });
    }, 50);
  });
  if (donePanel) $('[data-offline-send]', donePanel).addEventListener('click', deliver);
  window.addEventListener('online', function () { if (state.finished) deliver(); });
  setInterval(function () { if (state.finished && navigator.onLine !== false) deliver(); }, 15000);
  if (state.finished) showDonePanel();

  var discardDraft = $('[data-discard-draft]');
  if (discardDraft) discardDraft.addEventListener('click', function () {
    if (!confirm(gettext('Descartar este treino? Nada do que foi feito nele será guardado.'))) return;
    MT.removePending(state.uuid); MT.clearActive();
    window.location.href = root.dataset.myWorkoutsUrl;
  });

  // ---------- a draft is sent as soon as there is a connection, then the screen continues as the real session ----------
  var syncingDraft = false;
  function syncDraft() {
    if (!isDraft || syncingDraft || state.finished || navigator.onLine === false) return;
    if (Date.now() - lastTouch < 4000) return;   // do not swap the screen under the student's fingers
    syncingDraft = true;
    snapshot();
    MT.send(MT.payloadFrom(state, false)).then(function (result) {
      syncingDraft = false;
      if (!result.ok) return;
      state.session = String(result.data.session); state.draft = false; MT.saveActive(state);
      if (result.data.finished) window.location.href = result.data.summary_url;
      else window.location.replace(result.data.execute_url);
    });
  }
  if (isDraft) {
    window.addEventListener('online', syncDraft);
    setInterval(syncDraft, 8000);
    syncDraft();
  }

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
