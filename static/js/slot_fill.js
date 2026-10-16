/* "Preencher a agenda do pacote": shows the dates the server would create (end date included) and lets the trainer
   untick days without class. The server recomputes everything on submit; this script only fetches the preview. */
(function () {
    'use strict';
    var form = document.querySelector('[data-slot-fill]');
    if (!form) return;
    var box = form.querySelector('[data-fill-preview]');
    var button = form.querySelector('[data-fill-submit]');
    var url = form.getAttribute('data-preview-url');
    var skip = new Set();
    var keep = new Set();   // holidays the trainer ticked back on
    var timer = null, controller = null;
    var idleLabel = button.innerHTML;

    function params() {
        var data = new URLSearchParams();
        new FormData(form).forEach(function (value, key) {
            if (key === 'csrfmiddlewaretoken' || key === 'skip' || key === 'keep') return;
            data.append(key, value);
        });
        skip.forEach(function (day) { data.append('skip', day); });
        keep.forEach(function (day) { data.append('keep', day); });
        return data;
    }

    function refresh() {
        if (controller) controller.abort();
        controller = new AbortController();
        fetch(url + '?' + params().toString(), {credentials: 'same-origin', signal: controller.signal})
            .then(function (response) { return response.ok ? response.text() : ''; })
            .then(function (html) {
                if (!html) return;
                box.innerHTML = html;   // server-rendered, escaped by Django
                var preview = box.querySelector('[data-preview]');
                var count = preview ? parseInt(preview.getAttribute('data-count'), 10) : 0;
                if (count) {
                    button.innerHTML = '<i class="bi bi-calendar-check"></i> Preencher agenda (' + count + (count === 1 ? ' aula)' : ' aulas)');
                } else {
                    button.innerHTML = idleLabel;
                }
            })
            .catch(function () { /* aborted or offline: the form still works without the preview */ });
    }

    function later() {
        clearTimeout(timer);
        timer = setTimeout(refresh, 250);
    }

    form.addEventListener('change', function (event) {
        var target = event.target;
        if (target.matches && target.matches('[data-skip-date]')) {
            if (target.checked) skip.delete(target.getAttribute('data-skip-date'));
            else skip.add(target.getAttribute('data-skip-date'));
        }
        if (target.matches && target.matches('[data-keep-date]')) {
            if (target.checked) keep.add(target.getAttribute('data-keep-date'));
            else keep.delete(target.getAttribute('data-keep-date'));
        }
        later();
    });
    form.addEventListener('input', function (event) {
        if (event.target.type === 'time' || event.target.type === 'date' || event.target.type === 'number') later();
    });
    form.addEventListener('submit', function () {
        form.querySelectorAll('input[name="skip"], input[name="keep"]').forEach(function (el) { el.remove(); });
        [['skip', skip], ['keep', keep]].forEach(function (pair) {
            pair[1].forEach(function (day) {
                var input = document.createElement('input');
                input.type = 'hidden';
                input.name = pair[0];
                input.value = day;
                form.appendChild(input);
            });
        });
    });
    later();
})();
