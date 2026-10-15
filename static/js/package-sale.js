// "Vender pacote": picking a package fills the agreed price from the catalog; the trainer may change it (flagged
// "ajustado"). The per-class / total pair is calculated live (static/js/package-prices.js does the same on the catalog form).
(function () {
    'use strict';

    var form = document.querySelector('[data-sale-form]');
    if (!form) { return; }
    var radios = form.querySelectorAll('input[name=package]');
    var per = form.querySelector('[name=price_per_session]');
    var total = form.querySelector('[name=price_total]');
    var modes = form.querySelectorAll('input[name=pricing_mode]');
    var flag = form.querySelector('[data-adjusted]');
    var touched = false;

    function chosen() { return form.querySelector('input[name=package]:checked'); }
    function mode() {
        var picked = form.querySelector('input[name=pricing_mode]:checked');
        return picked ? picked.value : 'per_session';
    }
    function fixed(n) { return (Math.round(n * 100) / 100).toFixed(2); }
    function sessions() { var r = chosen(); return r ? Number(r.getAttribute('data-sessions')) || 0 : 0; }

    function recalc() {
        var n = sessions();
        var typed = mode() === 'per_session' ? per : total;
        var derived = mode() === 'per_session' ? total : per;
        typed.readOnly = false;
        derived.readOnly = true;
        var value = typed.value === '' ? NaN : Number(typed.value);
        derived.value = (isNaN(value) || !n) ? '' : (mode() === 'per_session' ? fixed(value * n) : fixed(value / n));
        var r = chosen();
        var base = r ? r.getAttribute('data-total') : '';
        flag.hidden = !(r && base !== '' && total.value !== '' && Math.abs(Number(total.value) - Number(base)) > 0.004);
    }

    radios.forEach(function (radio) {
        radio.addEventListener('change', function () {
            if (!touched) {   // never overwrite a price the trainer typed by hand
                per.value = radio.getAttribute('data-per') || '';
                total.value = radio.getAttribute('data-total') || '';
                var perMode = form.querySelector('input[name=pricing_mode][value=per_session]');
                if (perMode) { perMode.checked = true; }
            }
            recalc();
        });
    });
    [per, total].forEach(function (input) { input.addEventListener('input', function () { touched = true; recalc(); }); });
    modes.forEach(function (radio) { radio.addEventListener('change', recalc); });

    var first = chosen();
    if (first && per.value === '' && total.value === '') {
        per.value = first.getAttribute('data-per') || '';
        total.value = first.getAttribute('data-total') || '';
    }
    recalc();
})();
