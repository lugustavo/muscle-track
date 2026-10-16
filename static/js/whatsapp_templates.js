/* "Mensagens do WhatsApp": live preview while typing (the server renders it with an example student), buttons that
   insert a variable at the cursor, and the tab that opens from the address (#tab-mensagens). */
(function () {
    'use strict';
    document.querySelectorAll('[data-whatsapp-card]').forEach(function (card) {
        var area = card.querySelector('[data-wa-text]');
        var preview = card.querySelector('[data-wa-preview]');
        var errors = card.querySelector('[data-wa-errors]');
        var url = card.getAttribute('data-preview-url');
        var timer = null, controller = null;

        function refresh() {
            if (controller) controller.abort();
            controller = new AbortController();
            fetch(url + '?text=' + encodeURIComponent(area.value), {credentials: 'same-origin', signal: controller.signal})
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (data) {
                    if (!data) return;
                    preview.textContent = data.text;           // text only: never HTML
                    errors.textContent = data.errors.join(' ');
                })
                .catch(function () { /* aborted or offline: the saved preview stays */ });
        }
        area.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(refresh, 250); });

        card.querySelectorAll('[data-wa-var]').forEach(function (button) {
            button.addEventListener('click', function () {
                var token = '{' + button.getAttribute('data-wa-var') + '}';
                var start = area.selectionStart, end = area.selectionEnd;
                area.value = area.value.slice(0, start) + token + area.value.slice(end);
                area.focus();
                area.selectionStart = area.selectionEnd = start + token.length;
                refresh();
            });
        });
    });

    if (window.location.hash === '#tab-mensagens' && window.bootstrap) {
        var button = document.querySelector('[data-whatsapp-tab]');
        if (button) bootstrap.Tab.getOrCreateInstance(button).show();
    }
})();
