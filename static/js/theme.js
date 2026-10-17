// Color theme: light, dark or automatic (follows the device). Runs in <head>, before the CSS, so the page never flashes.
(function () {
    'use strict';

    var KEY = 'mt-theme';
    var CHOICES = ['auto', 'light', 'dark'];
    var BAR_COLOR = {light: '#FF6B6B', dark: '#1d2733'};
    var query = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

    function stored() {
        try {
            var value = window.localStorage.getItem(KEY);
            return CHOICES.indexOf(value) === -1 ? 'auto' : value;
        } catch (e) { return 'auto'; }
    }

    function resolve(choice) {
        if (choice === 'auto') { return query && query.matches ? 'dark' : 'light'; }
        return choice;
    }

    // Both metas (light/dark device) get the color of the chosen theme, so a manual choice wins over the device
    function paintBar(theme) {
        var metas = document.querySelectorAll('meta[name="theme-color"]');
        for (var i = 0; i < metas.length; i++) { metas[i].setAttribute('content', BAR_COLOR[theme]); }
    }

    function apply(choice, notify) {
        var theme = resolve(choice);
        var root = document.documentElement;
        root.setAttribute('data-bs-theme', theme);
        root.setAttribute('data-theme-choice', choice);
        paintBar(theme);
        if (notify) {
            document.dispatchEvent(new CustomEvent('mt-theme-change', {detail: {choice: choice, theme: theme}}));
        }
    }

    window.MTTheme = {
        get: function () { return stored(); },
        resolved: function () { return resolve(stored()); },
        set: function (choice) {
            if (CHOICES.indexOf(choice) === -1) { choice = 'auto'; }
            try { window.localStorage.setItem(KEY, choice); } catch (e) { /* private mode: applies to this page only */ }
            apply(choice, true);
        }
    };

    apply(stored(), false);
    // This script runs before the metas exist: paint them once the document is parsed
    document.addEventListener('DOMContentLoaded', function () { paintBar(resolve(stored())); });

    // "Automatic" follows the device live; other tabs follow a manual choice
    if (query) {
        var onSystemChange = function () { if (stored() === 'auto') { apply('auto', true); } };
        if (query.addEventListener) { query.addEventListener('change', onSystemChange); }
        else if (query.addListener) { query.addListener(onSystemChange); }
    }
    window.addEventListener('storage', function (event) { if (event.key === KEY) { apply(stored(), true); } });

    // The selector in the top bar (rendered by base.html): mark the current choice
    document.addEventListener('DOMContentLoaded', function () {
        var menu = document.querySelector('[data-theme-menu]');
        if (!menu) { return; }
        function refresh() {
            var choice = stored();
            menu.querySelectorAll('[data-theme-option]').forEach(function (item) {
                var on = item.getAttribute('data-theme-option') === choice;
                item.classList.toggle('active', on);
                item.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
            var icon = menu.querySelector('[data-theme-icon]');
            var current = menu.querySelector('[data-theme-option="' + choice + '"]');
            if (icon && current) { icon.className = 'bi ' + current.getAttribute('data-theme-icon-class'); }
            var toggle = menu.querySelector('[data-theme-toggle]');
            if (toggle && current) { toggle.setAttribute('aria-label', (window.interpolate && window.gettext
                ? window.interpolate(window.gettext('Tema: %s'), [current.getAttribute('data-theme-label')])
                : 'Tema: ' + current.getAttribute('data-theme-label'))); }
        }
        menu.addEventListener('click', function (event) {
            var item = event.target.closest('[data-theme-option]');
            if (!item) { return; }
            event.preventDefault();
            window.MTTheme.set(item.getAttribute('data-theme-option'));
        });
        document.addEventListener('mt-theme-change', refresh);
        refresh();
    });
})();
