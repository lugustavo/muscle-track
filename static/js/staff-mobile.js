// Trainer on a phone: the agenda shows one day at a time, picked in the day strip.
(function () {
    'use strict';

    var strip = document.querySelector('[data-day-strip]');
    if (!strip) { return; }
    var buttons = strip.querySelectorAll('[data-day]');
    var panes = document.querySelectorAll('[data-day-pane]');

    function select(day) {
        buttons.forEach(function (btn) {
            var on = btn.getAttribute('data-day') === day;
            btn.classList.toggle('selected', on);
            btn.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        panes.forEach(function (pane) {
            pane.classList.toggle('selected', pane.getAttribute('data-day-pane') === day);
        });
        try {
            var url = new URL(window.location.href);
            url.searchParams.set('dia', day);
            window.history.replaceState(null, '', url);
            keepDayInNext();
        } catch (e) { /* old browser: the strip still works */ }
    }

    // Buttons and links in the list return here (?next=): make "here" include the chosen day
    function keepDayInNext() {
        var here = window.location.pathname + window.location.search;
        document.querySelectorAll('[data-day-pane] a[href*="next="], [data-day-pane] form[action*="next="]').forEach(function (el) {
            var attr = el.tagName === 'A' ? 'href' : 'action';
            var target = new URL(el.getAttribute(attr), window.location.origin);
            var next = target.searchParams.get('next');
            if (next && next.indexOf(window.location.pathname) === 0) {
                target.searchParams.set('next', here);
                el.setAttribute(attr, target.pathname + target.search);
            }
        });
    }

    buttons.forEach(function (btn) {
        btn.addEventListener('click', function () { select(btn.getAttribute('data-day')); });
    });
})();
