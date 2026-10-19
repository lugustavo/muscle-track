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
    if (navigator.onLine === false) return;
    // one at a time, so a weak signal is not asked for everything at once
    var urls = (box.dataset.assets || '').split(' ').filter(Boolean);   // the code and styles of the workout screen
    routines.forEach(function (routine) { urls.push(routine.draft); if (routine.execute) urls.push(routine.execute); });
    (function next() {
        var url = urls.shift();
        if (!url) return;
        fetch(url, {credentials: 'same-origin', headers: {'X-Workout-Prefetch': '1'}}).catch(function () { /* offline: later */ }).then(next, next);
    })();
})();
