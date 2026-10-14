// Bottom bar: running-workout clock and hiding the bar while the on-screen keyboard is open.
(function () {
    'use strict';

    function pad(n) { return n < 10 ? '0' + n : String(n); }
    function format(seconds) {
        var h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
        return (h ? h + ':' + pad(m) : m) + ':' + pad(s);
    }

    var timers = document.querySelectorAll('[data-tabbar-timer]');
    function tick() {
        var now = Date.now();
        timers.forEach(function (el) {
            var seconds = Math.max(0, Math.floor((now - Number(el.getAttribute('data-tabbar-timer'))) / 1000));
            el.textContent = format(seconds);
        });
    }
    if (timers.length) { tick(); window.setInterval(tick, 1000); }

    var textual = 'input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]):not([type=file]), textarea, select';
    document.addEventListener('focusin', function (event) {
        if (event.target.matches && event.target.matches(textual)) { document.body.classList.add('kbd-open'); }
    });
    document.addEventListener('focusout', function () {
        window.setTimeout(function () {
            var active = document.activeElement;
            if (!(active && active.matches && active.matches(textual))) { document.body.classList.remove('kbd-open'); }
        }, 50);
    });
})();
