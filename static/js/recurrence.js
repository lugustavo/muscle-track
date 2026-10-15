// Repeat options: "Repetir esta aula" shows its options, and the end choice shows its date or number field.
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
})();
