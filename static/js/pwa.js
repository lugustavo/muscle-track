// "Behaves like an app": notice while offline, and an invitation to install (home screen) with a 30-day snooze.
(function () {
    'use strict';

    // ---- Offline notice ---------------------------------------------------------------------------------
    var banner = document.querySelector('[data-offline-banner]');
    var text = banner && banner.querySelector('[data-offline-text]');
    function renderConnection() {
        if (!banner) { return; }
        var offline = navigator.onLine === false;
        banner.classList.toggle('d-none', !offline);
        if (offline && text && document.querySelector('[data-workout-page]')) {
            text.textContent = 'Sem conexão. Pode continuar treinando: as séries ficam salvas no aparelho e são enviadas quando a internet voltar.';
        }
    }
    window.addEventListener('online', renderConnection);
    window.addEventListener('offline', renderConnection);
    renderConnection();

    // ---- Install invitation -----------------------------------------------------------------------------
    var card = document.querySelector('[data-install-card]');
    if (!card) { return; }
    var KEY = 'mt-install-dismissed';
    var SNOOZE_MS = 30 * 24 * 60 * 60 * 1000;
    var deferred = null;

    function standalone() {
        return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) || window.navigator.standalone === true;
    }
    function snoozed() {
        try {
            var at = Number(window.localStorage.getItem(KEY));
            return at && Date.now() - at < SNOOZE_MS;
        } catch (e) { return false; }
    }
    function snooze() {
        try { window.localStorage.setItem(KEY, String(Date.now())); } catch (e) { /* private mode */ }
        card.classList.add('d-none');
    }
    var ua = window.navigator.userAgent || '';
    var ios = /iPad|iPhone|iPod/.test(ua) || (window.navigator.platform === 'MacIntel' && window.navigator.maxTouchPoints > 1);

    function show(mode) {
        if (standalone() || snoozed()) { return; }
        var action = card.querySelector('[data-install-action]');
        card.querySelector('[data-install-text]').classList.toggle('d-none', mode === 'ios');
        card.querySelector('[data-install-ios]').classList.toggle('d-none', mode !== 'ios');
        action.classList.toggle('d-none', mode === 'ios');
        card.classList.remove('d-none');
    }

    window.addEventListener('beforeinstallprompt', function (event) {
        event.preventDefault();
        deferred = event;
        show('prompt');
    });
    window.addEventListener('appinstalled', function () { deferred = null; snooze(); });
    if (ios && !standalone()) { show('ios'); }   // Safari has no install event: explain the manual way

    card.querySelector('[data-install-action]').addEventListener('click', function () {
        if (!deferred) { return; }
        deferred.prompt();
        deferred.userChoice.then(function (choice) {
            deferred = null;
            if (choice && choice.outcome === 'accepted') { card.classList.add('d-none'); } else { snooze(); }
        });
    });
    card.querySelector('[data-install-dismiss]').addEventListener('click', snooze);
})();
