// Package prices: the trainer types the price per class or the total, and the other one is calculated live.
// Works on any form with data-package-form (catalog) or data-sale-form (sale), looking for these fields by name.
(function () {
    'use strict';

    var form = document.querySelector('[data-package-form], [data-sale-form]');
    if (!form) { return; }
    var sessionsInput = form.querySelector('[name=sessions]');
    var per = form.querySelector('[name=price_per_session]');
    var total = form.querySelector('[name=price_total]');
    var modes = form.querySelectorAll('input[name=pricing_mode]');
    if (!per || !total || !modes.length) { return; }

    function mode() {
        var picked = form.querySelector('input[name=pricing_mode]:checked');
        return picked ? picked.value : 'per_session';
    }
    function sessions() {
        if (sessionsInput) { return Number(sessionsInput.value) || 0; }
        return Number(form.getAttribute('data-sessions')) || 0;
    }
    function fixed(n) { return (Math.round(n * 100) / 100).toFixed(2); }

    function refresh() {
        var n = sessions();
        var typed = mode() === 'per_session' ? per : total;
        var derived = mode() === 'per_session' ? total : per;
        typed.readOnly = false;
        derived.readOnly = true;
        var value = typed.value === '' ? NaN : Number(typed.value);
        if (isNaN(value) || !n) { derived.value = ''; return; }
        derived.value = mode() === 'per_session' ? fixed(value * n) : fixed(value / n);
    }

    modes.forEach(function (radio) { radio.addEventListener('change', refresh); });
    [per, total, sessionsInput].forEach(function (input) { if (input) { input.addEventListener('input', refresh); } });
    refresh();
})();
