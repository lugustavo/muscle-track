// Repeat options: "Repetir esta aula" shows its options, and the end choice shows its date or number field.
// Weekly or monthly: the weekly-only parts and the monthly-only parts follow the choice; the monthly rules are
// worded for the start date by the server (the ones that do not fit that day are turned off).
(function () {
    'use strict';

    document.querySelectorAll('[data-repeat-box]').forEach(function (box) {
        var toggle = box.querySelector('input[type="checkbox"][name$="enabled"]');
        var body = box.querySelector('[data-repeat-body]');
        if (!toggle || !body) { return; }
        function sync() { body.hidden = !toggle.checked; }
        toggle.addEventListener('change', sync);
        sync();
    });

    document.querySelectorAll('[data-recurrence]').forEach(function (group) {
        var choices = group.querySelectorAll('[data-ends-choice]');
        var onField = group.querySelector('[data-ends-on]');
        var afterField = group.querySelector('[data-ends-after]');
        function sync() {
            var current = 'never';
            choices.forEach(function (radio) { if (radio.checked) { current = radio.value; } });
            if (onField) { onField.style.visibility = current === 'on' ? 'visible' : 'hidden'; }
            if (afterField) { afterField.style.visibility = current === 'after' ? 'visible' : 'hidden'; }
        }
        choices.forEach(function (radio) { radio.addEventListener('change', sync); });
        sync();
    });

    document.querySelectorAll('[data-recurrence]').forEach(function (group) {
        var form = group.closest('form');
        var radios = group.querySelectorAll('[data-frequency-choice]');
        var rule = group.querySelector('select[name$="month_rule"]');
        if (!form || !radios.length) { return; }
        var weeklyOnly = form.querySelectorAll('[data-weekly-only]');
        var monthlyOnly = form.querySelectorAll('[data-monthly-only]');
        var startInput = form.querySelector('input[name$="starts_on"]') || form.querySelector('input[name="date"]');
        var timer = null;

        function monthly() {
            var on = false;
            radios.forEach(function (radio) { if (radio.checked && radio.value === 'monthly') { on = true; } });
            return on;
        }
        function startDay() {
            if (startInput && startInput.value) { return startInput.value; }
            var d = new Date();
            return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
        }
        function loadRules() {
            if (!rule || !monthly()) { return; }
            var url = group.getAttribute('data-month-url') + '?date=' + encodeURIComponent(startDay());
            fetch(url, {credentials: 'same-origin', headers: {'Accept': 'application/json'}})
                .then(function (response) { return response.ok ? response.json() : null; })
                .then(function (data) {
                    if (!data) { return; }
                    Array.prototype.forEach.call(rule.options, function (option) {
                        if (!option.dataset.generic) { option.dataset.generic = option.textContent; }
                        var text = data.options[option.value];
                        option.disabled = !text;
                        option.textContent = text || option.dataset.generic;   // the ones that do not fit keep the plain wording
                    });
                    if (rule.selectedOptions[0] && rule.selectedOptions[0].disabled) {
                        var first = Array.prototype.find.call(rule.options, function (option) { return !option.disabled; });
                        if (first) { rule.value = first.value; }
                    }
                })
                .catch(function () { /* the generic wording stays */ });
        }
        function sync() {
            var on = monthly();
            weeklyOnly.forEach(function (el) { el.hidden = on; });
            monthlyOnly.forEach(function (el) { el.hidden = !on; });
            loadRules();
        }
        radios.forEach(function (radio) { radio.addEventListener('change', sync); });
        if (startInput) {
            startInput.addEventListener('change', function () { clearTimeout(timer); timer = setTimeout(loadRules, 150); });
        }
        sync();
    });
})();
