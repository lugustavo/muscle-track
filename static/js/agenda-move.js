// Agenda: move a marked class to another day (drag and drop on a computer) or to any day/time ("Mover" sheet, works on touch),
// with an "Desfazer" toast. The server does the work (appointment_move / appointment_move_undo); the page reloads to redraw.
(function () {
    'use strict';

    // Django's JS catalog (/jsi18n/); identity fallbacks if it has not loaded
    var gettext = window.gettext || function (s) { return s; };
    var interpolate = window.interpolate || function (s, o) {
        return s.replace(/%\((\w+)\)s/g, function (m, k) { return String(o[k]); });
    };

    var KEY = 'agenda-move-toast';
    var modalEl = document.getElementById('moveModal');
    var toastEl = document.querySelector('[data-agenda-toast]');
    var tokenInput = modalEl ? modalEl.querySelector('[name=csrfmiddlewaretoken]') : document.querySelector('[name=csrfmiddlewaretoken]');
    var dragged = null;

    function post(url, data) {
        var body = new URLSearchParams(data || {});
        body.set('csrfmiddlewaretoken', tokenInput ? tokenInput.value : '');
        return fetch(url, {
            method: 'POST', body: body, credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' }
        }).then(function (response) {
            return response.json().catch(function () { return { ok: false, error: gettext('Não foi possível concluir. Tente de novo.') }; });
        }, function () { return { ok: false, error: gettext('Sem conexão. Tente de novo.') }; });
    }

    function remember(info) {
        try { window.sessionStorage.setItem(KEY, JSON.stringify(info)); } catch (e) { /* private mode: no toast after reload */ }
    }

    function done(result) {
        remember({
            text: interpolate(gettext('Aula de %(person)s movida para %(when)s.'), { person: result.person, when: result.when }, true),
            overlap: result.overlap || [], undo: result.undo_url
        });
        window.location.reload();
    }

    function showToast(info) {
        if (!toastEl) { return; }
        var text = toastEl.querySelector('[data-toast-text]');
        var warn = toastEl.querySelector('[data-toast-warn]');
        var undo = toastEl.querySelector('[data-toast-undo]');
        text.textContent = info.text;
        warn.textContent = info.overlap && info.overlap.length ? interpolate(gettext('Atenção: se sobrepõe a outra aula (%(classes)s).'), { classes: info.overlap.join(', ') }, true) : '';
        warn.hidden = !warn.textContent;
        undo.hidden = !info.undo;
        undo.onclick = function () {
            undo.disabled = true;
            post(info.undo).then(function (result) {
                if (result.ok) {
                    remember({ text: gettext('Movimento desfeito.') });
                    window.location.reload();
                } else {
                    undo.disabled = false;
                    text.textContent = result.error;
                    undo.hidden = true;
                }
            });
        };
        toastEl.hidden = false;
        window.clearTimeout(showToast.timer);
        showToast.timer = window.setTimeout(function () { toastEl.hidden = true; }, 12000);
    }

    var closeToast = toastEl && toastEl.querySelector('[data-toast-close]');
    if (closeToast) { closeToast.addEventListener('click', function () { toastEl.hidden = true; }); }

    try {
        var saved = window.sessionStorage.getItem(KEY);
        if (saved) {
            window.sessionStorage.removeItem(KEY);
            showToast(JSON.parse(saved));
        }
    } catch (e) { /* ignore */ }

    // Drag and drop (pointer devices): drop a card on another day
    function targetDate(node) {
        var target = node.closest ? node.closest('[data-day-pane], [data-agenda-day]') : null;
        return target ? { el: target, date: target.getAttribute('data-day-pane') || target.getAttribute('data-agenda-day') } : null;
    }

    document.addEventListener('dragstart', function (event) {
        var card = event.target.closest && event.target.closest('[data-appt-move]');
        if (!card) { return; }
        dragged = card;
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', card.getAttribute('data-appt-person'));
        card.classList.add('dragging');
    });
    document.addEventListener('dragend', function () {
        if (dragged) { dragged.classList.remove('dragging'); }
        dragged = null;
        document.querySelectorAll('.drop-ok').forEach(function (el) { el.classList.remove('drop-ok'); });
    });
    document.addEventListener('dragover', function (event) {
        if (!dragged) { return; }
        var target = targetDate(event.target);
        document.querySelectorAll('.drop-ok').forEach(function (el) { if (!target || el !== target.el) { el.classList.remove('drop-ok'); } });
        if (target && target.date !== dragged.getAttribute('data-appt-date')) {
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
            target.el.classList.add('drop-ok');
        }
    });
    document.addEventListener('drop', function (event) {
        if (!dragged) { return; }
        var target = targetDate(event.target);
        if (!target || target.date === dragged.getAttribute('data-appt-date')) { return; }
        event.preventDefault();
        var url = dragged.getAttribute('data-appt-move');
        target.el.classList.remove('drop-ok');
        post(url, { date: target.date }).then(function (result) {
            if (result.ok) { done(result); } else { showToast({ text: result.error }); }
        });
    });

    // "Mover" sheet: any day, time and length
    if (!modalEl || !window.bootstrap) { return; }
    var modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
    var form = modalEl.querySelector('form');
    var error = modalEl.querySelector('[data-move-error]');
    var more = modalEl.querySelector('[data-move-more]');
    var current = null;

    document.addEventListener('click', function (event) {
        var opener = event.target.closest && event.target.closest('[data-appt-move-open]');
        if (!opener) { return; }
        var card = opener.closest('[data-appt-move]');
        if (!card) { return; }
        event.preventDefault();
        current = card;
        modalEl.querySelector('[data-move-person]').textContent = card.getAttribute('data-appt-person');
        form.elements.date.value = card.getAttribute('data-appt-date');
        form.elements.time.value = card.getAttribute('data-appt-time');
        form.elements.duration.value = card.getAttribute('data-appt-duration');
        more.setAttribute('href', opener.getAttribute('href'));
        error.hidden = true;
        modal.show();
    });

    form.addEventListener('submit', function (event) {
        event.preventDefault();
        if (!current) { return; }
        var button = form.querySelector('[type=submit]');
        button.disabled = true;
        post(current.getAttribute('data-appt-move'), {
            date: form.elements.date.value, time: form.elements.time.value, duration: form.elements.duration.value
        }).then(function (result) {
            if (result.ok) { done(result); return; }
            button.disabled = false;
            error.textContent = result.error;
            error.hidden = false;
        });
    });
})();
