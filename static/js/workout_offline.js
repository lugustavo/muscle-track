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
    // Resolves {ok, status, data, kind}. ok means the server really applied it (not a login page after a lost session).
    // kind says why it did not: 'network' (no answer / timed out), 'login' (the session is gone), 'refused' (400/403/404), 'server'.
    function send(payload, url) {
        var controller = window.AbortController ? new AbortController() : null;
        var timer = controller ? setTimeout(function () { controller.abort(); }, 25000) : null;   // a half-open connection must not hang forever
        return fetch(url || SYNC_URL, {
            method: 'POST', credentials: 'same-origin', signal: controller ? controller.signal : undefined,
            headers: {'X-CSRFToken': csrf(), 'Content-Type': 'application/json'}, body: JSON.stringify(payload)
        }).then(function (response) {
            clearTimeout(timer);
            if (response.redirected || response.status === 401) return {ok: false, status: response.status, data: {}, kind: 'login'};
            return response.json().then(function (data) {
                var ok = response.ok && data.success === true;
                var kind = ok ? '' : (response.status >= 500 ? 'server' : 'refused');
                return {ok: ok, status: response.status, data: data, kind: kind};
            }, function () { return {ok: false, status: response.status, data: {}, kind: response.status >= 500 ? 'server' : 'refused'}; });
        }, function () { clearTimeout(timer); return {ok: false, status: 0, data: {}, kind: 'network'}; });
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
    // Send every waiting workout, one at a time. Resolves the list of {payload, data} the server accepted; its `failure`
    // property says why the last one was not ({kind, status}). A workout the server refused stays on the device (marked
    // with the error, so it is not retried over and over) and is only tried again when `force` is set (the student asked).
    // `force` also ignores navigator.onLine, which some phones leave stale after the connection comes back.
    function flushPending(force) {
        if (flushing) return flushing;
        var delivered = [];
        function next() {
            var list = pending().filter(function (item) { return force || !item.error; });
            if (!list.length || (!force && navigator.onLine === false)) return Promise.resolve(delivered);
            var payload = list[0];
            return send(payload).then(function (result) {
                if (result.ok) {
                    removePending(payload.uuid);
                    var state = active();
                    if (state && state.uuid === payload.uuid && result.data.finished) clearActive();
                    delivered.push({payload: payload, data: result.data});
                    return next();
                }
                delivered.failure = {kind: result.kind, status: result.status};
                if (result.kind === 'refused' && result.status !== 403 && result.status !== 429) {   // it will not be accepted as it is
                    payload.error = result.status;
                    addPending(payload);
                    if (!force) return next();   // do not block the others
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

    // ---- the app ready for no connection ------------------------------------------------------------------------------
    // The student's main screens and the workouts' screens are fetched through the service worker (it keeps them,
    // templates/pwa/sw.js) so the app opens at the gym with no signal. Quietly, one at a time, at most every 20 minutes.
    var WARMED = 'mt-warm-v1';
    var WARM_EVERY = 20 * 60 * 1000;
    var PAGES = ['/', '/my-workouts/', '/my-agenda/', '/my-package/', '/my-history/'];
    var FILES = /\/(?:static|media\/exercises)\/[^"'\s)<>]+/g;   // code, styles, charts and exercise pictures a screen uses

    // The workouts listed on "Meus treinos" (from its HTML), kept for starting them with no connection
    function learnRoutines(html) {
        var box = new DOMParser().parseFromString(html, 'text/html').querySelector('[data-offline-routines]');
        if (!box) return [];
        var list = Array.prototype.map.call(box.querySelectorAll('[data-routine]'), function (el) {
            return {id: el.dataset.routine, name: el.dataset.name, draft: el.dataset.draft, execute: el.dataset.execute || ''};
        });
        saveRoutines(list);
        var urls = [];
        list.forEach(function (routine) { urls.push(routine.draft); if (routine.execute) urls.push(routine.execute); });
        return urls;
    }

    // Fetch each screen in turn (a weak signal is not asked for everything at once), then the files they use that the
    // service worker does not have yet. Resolves when done.
    function warmUrls(urls) {
        var queue = urls.slice(), files = {};
        function offline() { return navigator.onLine === false; }
        function nextPage() {
            var url = queue.shift();
            if (!url || offline()) return Promise.resolve();
            return fetch(url, {credentials: 'same-origin', headers: {'X-Workout-Prefetch': '1'}}).then(function (response) {
                var type = response.headers.get('Content-Type') || '';
                if (!response.ok || type.indexOf('text/html') === -1) return;
                return response.text().then(function (html) {
                    (html.match(FILES) || []).forEach(function (f) { files[f] = true; });
                    if (url === '/my-workouts/') learnRoutines(html).forEach(function (u) { if (queue.indexOf(u) === -1) queue.push(u); });
                });
            }).catch(function () { /* offline: later */ }).then(nextPage);
        }
        function nextFile() {
            var url = Object.keys(files).shift();
            if (!url || offline()) return Promise.resolve();
            delete files[url];
            var have = window.caches ? caches.match(url) : Promise.resolve(null);
            return have.then(function (hit) { return hit ? null : fetch(url, {credentials: 'same-origin'}); })
                .catch(function () { /* later */ }).then(nextFile);
        }
        return nextPage().then(nextFile);
    }

    function warm(extra, force) {
        if (navigator.onLine === false) return;
        if (navigator.connection && navigator.connection.saveData) return;   // the student asked the phone to spare data
        var last = Number(read(WARMED, 0));
        if (!force && Date.now() - last < WARM_EVERY) return;
        write(WARMED, Date.now());
        var here = window.location.pathname;
        var urls = PAGES.filter(function (page) { return page !== here; });
        // the list of workouts comes first: its screens are the ones that matter at the gym
        urls.sort(function (x, y) { return (y === '/my-workouts/') - (x === '/my-workouts/'); });
        (extra || []).forEach(function (url) { if (urls.indexOf(url) === -1) urls.push(url); });
        warmUrls(urls);
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

    // The workouts the student can start: kept from "Meus treinos" (their screens are fetched along with the main ones)
    function routineUrls() {
        var urls = [];
        routines().forEach(function (routine) { urls.push(routine.draft); if (routine.execute) urls.push(routine.execute); });
        return urls;
    }

    window.MTWorkout = {
        warm: warm, routineUrls: routineUrls, warmUrls: warmUrls,
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
    // not while a workout is open (it must have the connection for itself), and after the page has settled
    if (!document.getElementById('workout')) {
        setTimeout(function () { warm(routineUrls()); }, 4000);
        window.addEventListener('online', function () { setTimeout(function () { warm(routineUrls()); }, 2000); });
    }
})();
