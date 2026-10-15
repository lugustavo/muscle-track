// "Alterar plano": picking a catalog plan fills the name and the value (unless they were changed by hand),
// flags a value that differs from the plan's, and offers quick renewals for the expiry date.
(function () {
    'use strict';

    var form = document.querySelector('[data-contract-form]');
    if (!form) { return; }
    var radios = form.querySelectorAll('input[name=plan]');
    var nameInput = form.querySelector('[name=plan_name]');
    var priceInput = form.querySelector('[name=plan_price]');
    var expiry = form.querySelector('[name=plan_expires_on]');
    var adjusted = form.querySelector('[data-adjusted]');
    var previous = checked();

    function checked() {
        var radio = form.querySelector('input[name=plan]:checked');
        return radio ? { name: radio.getAttribute('data-plan-name') || '', price: radio.getAttribute('data-plan-price') || '' } : { name: '', price: '' };
    }
    function same(a, b) { return String(a).trim() === String(b).trim(); }
    function numbersEqual(a, b) { return a !== '' && b !== '' && Number(a) === Number(b); }

    function refreshFlag() {
        var plan = checked();
        adjusted.hidden = !(plan.price !== '' && priceInput.value !== '' && !numbersEqual(priceInput.value, plan.price));
    }

    radios.forEach(function (radio) {
        radio.addEventListener('change', function () {
            var plan = checked();
            // overwrite only what is empty or still holds the previous plan's value (never a hand-typed one)
            if (plan.name && (nameInput.value === '' || same(nameInput.value, previous.name))) { nameInput.value = plan.name; }
            if (plan.price !== '' && (priceInput.value === '' || numbersEqual(priceInput.value, previous.price))) { priceInput.value = plan.price; }
            previous = plan;
            refreshFlag();
        });
    });
    priceInput.addEventListener('input', refreshFlag);

    form.querySelector('[data-restore]').addEventListener('click', function () {
        var plan = checked();
        if (plan.price !== '') { priceInput.value = plan.price; }
        if (plan.name) { nameInput.value = plan.name; }
        refreshFlag();
    });

    function pad(n) { return n < 10 ? '0' + n : String(n); }
    form.querySelectorAll('[data-add-months]').forEach(function (button) {
        button.addEventListener('click', function () {
            var months = Number(button.getAttribute('data-add-months'));
            var base = new Date();
            base.setHours(0, 0, 0, 0);
            if (expiry.value) {
                var parts = expiry.value.split('-');
                var current = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
                if (current > base) { base = current; }
            }
            var day = base.getDate();
            var target = new Date(base.getFullYear(), base.getMonth() + months, 1);
            var last = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
            target.setDate(Math.min(day, last));   // 31 Jan + 1 month = 28/29 Feb, never March
            expiry.value = target.getFullYear() + '-' + pad(target.getMonth() + 1) + '-' + pad(target.getDate());
        });
    });

    refreshFlag();
})();
