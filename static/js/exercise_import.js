// Import animations from wger: select all / none, a running count, and the submit button only when something is picked.
(function () {
    'use strict';
    var form = document.querySelector('[data-wger-import]');
    if (!form) { return; }
    var picks = Array.prototype.slice.call(form.querySelectorAll('[data-pick]'));
    var submit = form.querySelector('[data-submit]');
    var label = form.querySelector('[data-count-label]');
    var max = parseInt(form.getAttribute('data-max'), 10) || 15;
    var texts = {
        one: form.getAttribute('data-text-one') || '1 selecionado',
        many: form.getAttribute('data-text-many') || '{n} selecionados',
        limit: form.getAttribute('data-text-limit') || 'No máximo {max} de cada vez.'
    };

    function refresh() {
        var n = picks.filter(function (box) { return box.checked; }).length;
        submit.disabled = n === 0;
        var text = n === 1 ? texts.one : texts.many.replace('{n}', n);
        if (n > max) { text += ' - ' + texts.limit.replace('{max}', max); }
        label.textContent = n ? text : '';
    }
    function setAll(value) {
        picks.forEach(function (box) { box.checked = value; });
        refresh();
    }

    form.addEventListener('change', function (event) { if (event.target.matches('[data-pick]')) { refresh(); } });
    var all = form.querySelector('[data-select-all]');
    var none = form.querySelector('[data-select-none]');
    if (all) { all.addEventListener('click', function () { setAll(true); }); }
    if (none) { none.addEventListener('click', function () { setAll(false); }); }
    refresh();
})();
