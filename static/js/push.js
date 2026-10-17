// Notifications page: subscribe this browser to web push and hand the subscription to the server.
(function () {
    'use strict';

    // Django's JS catalog (/jsi18n/); identity fallback if it has not loaded
    var gettext = window.gettext || function (s) { return s; };

    var card = document.querySelector('[data-push-card]');
    if (!card) { return; }
    var button = card.querySelector('[data-push-enable]');
    var status = card.querySelector('[data-push-status]');
    var tokenInput = document.querySelector('[name=csrfmiddlewaretoken]');

    function say(text, bad) {
        status.textContent = text;
        status.className = 'small mt-2 ' + (bad ? 'text-danger' : 'text-success');
    }

    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
        button.disabled = true;
        say(gettext('Este navegador não aceita avisos no aparelho. No iPhone, adicione o app à tela de início primeiro.'), true);
        return;
    }

    function keyBytes(base64url) {
        var padding = '='.repeat((4 - base64url.length % 4) % 4);
        var raw = window.atob((base64url + padding).replace(/-/g, '+').replace(/_/g, '/'));
        var out = new Uint8Array(raw.length);
        for (var i = 0; i < raw.length; i++) { out[i] = raw.charCodeAt(i); }
        return out;
    }

    button.addEventListener('click', function () {
        button.disabled = true;
        say(gettext('Ativando…'));
        Notification.requestPermission().then(function (permission) {
            if (permission !== 'granted') { throw new Error(gettext('Permissão negada. Libere as notificações nas configurações do navegador.')); }
            return navigator.serviceWorker.ready;
        }).then(function (registration) {
            return registration.pushManager.getSubscription().then(function (existing) {
                return existing || registration.pushManager.subscribe({
                    userVisibleOnly: true, applicationServerKey: keyBytes(card.getAttribute('data-public-key'))
                });
            });
        }).then(function (subscription) {
            return fetch(card.getAttribute('data-subscribe-url'), {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': tokenInput ? tokenInput.value : '' },
                body: JSON.stringify(subscription.toJSON())
            });
        }).then(function (response) {
            return response.json().then(function (data) {
                if (!data.ok) { throw new Error(data.error || gettext('Não foi possível ativar.')); }
                say(gettext('Pronto! Este aparelho vai receber os avisos.'));
                window.setTimeout(function () { window.location.reload(); }, 800);
            });
        }).catch(function (error) {
            button.disabled = false;
            say(error.message || gettext('Não foi possível ativar.'), true);
        });
    });
})();
