/* Workouts with a weak or no connection: what the device keeps and how it sends it (server side: core/workout_offline.py).

   - the ACTIVE state is the workout being done on this device (timer, every set, comments), written as it changes, so the
     screen can be reopened offline and nothing depends on the page staying open;
   - a finished workout that has not reached the server waits in PENDING (the full payload) and is sent as soon as there
     is a connection, from whatever page of the app is open. Sending is idempotent (the workout has an id of its own);
   - the one-set queue of the workout screen (mt-queue-v1) is also flushed from other pages.
   Loaded on every page of a student; the workout screen (workout.js) uses it for its own state. */
(function () {
    'use strict';
    var gettext = window.gettext || function (s) { return s; };

    var ACTIVE = 'mt-wo-active-v1';
    var PENDING = 'mt-wo-pending-v1';
    var ROUTINES = 'mt-wo-routines-v1';
    var QUEUE = 'mt-queue-v1';
    var CACHE = 'muscle-track-workout-v1';   // the service worker's copy of the workout screens (templates/pwa/sw.js)
    var SYNC_URL = '/api/workout-sync/';

    function read(key, fallback) {
        try { var raw = localStorage.getItem(key); return raw === null ? fallback : JSON.parse(raw); } catch (e) { return fallback; }
    }
    function write(key, value) {
        try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (e) { return false; }   // storage full or blocked
    }
    function drop(key) { try { localStorage.removeItem(key); } catch (e) { /* ignore */ } }

    function uuid() {
        if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
        var bytes = new Uint8Array(16);
        (window.crypto || window.msCrypto).getRandomValues(bytes);
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        var hex = Array.prototype.map.call(bytes, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
        return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
    }

    function csrf() {
        var match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
        if (match) return decodeURIComponent(match[1]);
        var page = document.getElementById('workout');
        return page ? page.dataset.csrf : '';
    }

    // ---- the workout being done on this device ----------------------------------------------------------------------
    // {uuid, routine, routineName, session|null, draft, startedAt, pausedSeconds, pausedAt, notes, finished, endedAt,
    //  exercises: {prx: {notes, sets: {n: {w, r, d, t}}}}, url}
    function active() { return read(ACTIVE, null); }
    function saveActive(state) { state.savedAt = Date.now(); return write(ACTIVE, state); }
    function clearActive() { drop(ACTIVE); }

    function payloadFrom(state, finish) {
        var exercises = Object.keys(state.exercises || {}).map(function (prx) {
            var exercise = state.exercises[prx];
            return {
                prx: parseInt(prx, 10), notes: exercise.notes || '',
                sets: Object.keys(exercise.sets || {}).map(function (n) {
                    var set = exercise.sets[n];
                    return {n: parseInt(n, 10), weight: set.w === undefined ? null : set.w, reps: set.r === undefined ? null : set.r,
                            done: !!set.d, at: set.t || null};
                })
            };
        });
        var payload = {
            uuid: state.uuid, routine: parseInt(state.routine, 10), session: state.session ? parseInt(state.session, 10) : null,
            started_at: state.startedAt, paused_seconds: Math.round(state.pausedSeconds || 0), paused: !!state.pausedAt,
            paused_at: state.pausedAt || null, notes: state.notes || '', exercises: exercises, finish: !!finish,
            routine_name: state.routineName || ''
        };
        if (finish) payload.ended_at = state.endedAt || Date.now();
        return payload;
    }

    // ---- sending ------------------------------------------------------------------------------------------------------
    // Resolves {ok, status, data}. ok means the server really applied it (not a login page after a lost session).
    function send(payload, url) {
        return fetch(url || SYNC_URL, {
            method: 'POST', credentials: 'same-origin',
            headers: {'X-CSRFToken': csrf(), 'Content-Type': 'application/json'}, body: JSON.stringify(payload)
        }).then(function (response) {
            return response.json().then(function (data) {
                return {ok: response.ok && !response.redirected && data.success === true, status: response.status, data: data};
            }, function () { return {ok: false, status: response.status, data: {}}; });
        }, function () { return {ok: false, status: 0, data: {}}; });
    }

    function pending() { return read(PENDING, []); }
    function addPending(payload) {
        var list = pending().filter(function (item) { return item.uuid !== payload.uuid; });
        list.push(payload);
        write(PENDING, list);
    }
    function removePending(id) {
        write(PENDING, pending().filter(function (item) { return item.uuid !== id; }));
    }

    var flushing = null;
    // Send every waiting workout, one at a time. Resolves the list of {payload, data} the server accepted.
    function flushPending() {
        if (flushing) return flushing;
        var delivered = [];
        function next() {
            var list = pending();
            if (!list.length || navigator.onLine === false) return Promise.resolve(delivered);
            var payload = list[0];
            return send(payload).then(function (result) {
                if (result.ok) {
                    removePending(payload.uuid);
                    var state = active();
                    if (state && state.uuid === payload.uuid && result.data.finished) clearActive();
                    delivered.push({payload: payload, data: result.data});
                    return next();
                }
                if (result.status === 400 || result.status === 404) {   // it can never be accepted: do not block the others
                    removePending(payload.uuid);
                    var rejected = active();
                    if (rejected && rejected.uuid === payload.uuid) clearActive();
                    return next();
                }
                return delivered;   // no connection, server busy or not logged in: try again later
            });
        }
        flushing = next().then(function (done) { flushing = null; return done; }, function () { flushing = null; return delivered; });
        return flushing;
    }

    // The one-set queue of the workout screen, flushed from any other page (the screen itself does it while open)
    function flushQueue() {
        var queue = read(QUEUE, []);
        if (!queue.length || navigator.onLine === false) return Promise.resolve();
        function step() {
            queue = read(QUEUE, []);
            if (!queue.length) return Promise.resolve();
            var item = queue[0];
            return fetch(item.url, {method: 'POST', credentials: 'same-origin', headers: {'X-CSRFToken': csrf(), 'Content-Type': 'application/json'},
                                     body: JSON.stringify(item.body)})
                .then(function (response) {
                    if (response.ok || (response.status >= 400 && response.status < 500 && response.status !== 429)) {
                        queue.shift(); write(QUEUE, queue);
                        return step();
                    }
                }, function () { /* offline: later */ });
        }
        return step();
    }

    // ---- the routines the student can start with no connection (kept when "Meus treinos" is opened online) -------------
    function routines() { return read(ROUTINES, []); }
    function saveRoutines(list) { write(ROUTINES, list); }

    // Forget the kept copies of the workout screens in progress (the ones of workouts not yet started stay for offline starts)
    function clearCopies() {
        if (!(window.caches && caches.open)) return;
        caches.open(CACHE).then(function (cache) {
            return cache.keys().then(function (keys) {
                keys.forEach(function (request) { if (/\/execute\/$/.test(new URL(request.url).pathname)) cache.delete(request); });
            });
        }).catch(function () { /* ignore */ });
    }

    // ---- notices on any page ------------------------------------------------------------------------------------------
    function notice(text, href, linkText) {
        var box = document.createElement('div');
        box.className = 'alert alert-success shadow position-fixed start-50 translate-middle-x py-2 px-3 mb-0';
        box.style.cssText = 'bottom: 5.5rem; z-index: 3000; max-width: 92vw;';
        box.setAttribute('role', 'status');
        box.textContent = text + ' ';
        if (href) {
            var link = document.createElement('a');
            link.href = href;
            link.textContent = linkText;
            link.className = 'alert-link';
            box.appendChild(link);
        }
        document.body.appendChild(box);
        setTimeout(function () { box.remove(); }, 8000);
    }

    function autoFlush() {
        if (document.getElementById('workout')) return;   // the workout screen sends its own
        flushQueue();
        flushPending().then(function (delivered) {
            var done = delivered.filter(function (d) { return d.data.finished; });
            if (!done.length) return;
            var last = done[done.length - 1];
            notice(gettext('Treino enviado!'), last.data.summary_url, gettext('Ver resumo'));
        });
    }

    window.MTWorkout = {
        uuid: uuid, csrf: csrf, active: active, saveActive: saveActive, clearActive: clearActive, payloadFrom: payloadFrom,
        send: send, pending: pending, addPending: addPending, removePending: removePending, flushPending: flushPending,
        routines: routines, saveRoutines: saveRoutines, clearCopies: clearCopies, notice: notice
    };

    // The summary page means the workout reached the server and was finished: the local copy is no longer needed
    var summary = document.querySelector('[data-workout-summary]');
    if (summary) {
        var state = active();
        if (state && String(state.session) === summary.dataset.session) clearActive();
        clearCopies();
    }

    autoFlush();
    window.addEventListener('online', autoFlush);
})();
