/* "Meus treinos": remember which workouts can be started with no connection and keep their screens ready.
   The service worker keeps each screen it fetches here (templates/pwa/sw.js); static/js/workout_offline.js keeps the list. */
(function () {
    'use strict';
    var box = document.querySelector('[data-offline-routines]');
    if (!box || !window.MTWorkout) return;
    var routines = Array.prototype.map.call(box.querySelectorAll('[data-routine]'), function (el) {
        return {id: el.dataset.routine, name: el.dataset.name, draft: el.dataset.draft, execute: el.dataset.execute || ''};
    });
    window.MTWorkout.saveRoutines(routines);
    // With no connection "Iniciar treino" (a POST) cannot work: start the draft kept on this device instead
    document.addEventListener('submit', function (event) {
        var match = /\/my-workouts\/(\d+)\/start\/$/.exec(event.target.getAttribute('action') || '');
        if (!match || navigator.onLine !== false) return;
        var routine = routines.filter(function (r) { return r.id === match[1]; })[0];
        if (!routine) return;
        event.preventDefault();
        window.location.href = routine.draft;
    });
    if (navigator.onLine === false) return;
    // the code and styles of the workout screen first, then the screens of the workouts (and their pictures), now
    var assets = (box.dataset.assets || '').split(' ').filter(Boolean);
    assets.reduce(function (chain, url) {
        return chain.then(function () { return fetch(url, {credentials: 'same-origin'}).catch(function () { /* offline: later */ }); });
    }, Promise.resolve()).then(function () { window.MTWorkout.warm(window.MTWorkout.routineUrls(), true); });
})();
