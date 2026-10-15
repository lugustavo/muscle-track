// Quick search for the trainer: "/" or Ctrl/Cmd+K (or the magnifier) opens a sheet with live results.
// Text from the server (student names...) is only ever inserted with textContent, never as HTML.
(function () {
    'use strict';

    var RECENT_KEY = 'mt-search-recent';
    var RECENT_MAX = 5;
    var sheet = document.getElementById('searchSheet');

    function readRecent() {
        try {
            var list = JSON.parse(window.localStorage.getItem(RECENT_KEY) || '[]');
            return Array.isArray(list) ? list.filter(function (x) { return typeof x === 'string'; }).slice(0, RECENT_MAX) : [];
        } catch (e) { return []; }
    }
    function remember(query) {
        query = (query || '').trim();
        if (query.length < 2) { return; }
        var list = readRecent().filter(function (x) { return x.toLowerCase() !== query.toLowerCase(); });
        list.unshift(query);
        try { window.localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX))); } catch (e) { /* private mode */ }
    }
    function clearRecent() {
        try { window.localStorage.removeItem(RECENT_KEY); } catch (e) { /* private mode */ }
    }

    // On the results page: that search counts as recent
    var page = document.querySelector('[data-search-page] input[name="q"]');
    if (page) { remember(page.value); }
    if (!sheet || !window.bootstrap) { return; }

    var input = sheet.querySelector('[data-search-input]');
    var results = sheet.querySelector('[data-search-results]');
    var status = sheet.querySelector('[data-search-status]');
    var recentBox = sheet.querySelector('[data-search-recent]');
    var recentList = sheet.querySelector('[data-search-recent-list]');
    var form = sheet.querySelector('[data-search-form]');
    var suggestUrl = sheet.getAttribute('data-suggest-url');
    var timer = null;
    var controller = null;
    var active = -1;

    function open() { window.bootstrap.Offcanvas.getOrCreateInstance(sheet).show(); }
    function items() { return results.querySelectorAll('[data-search-item]'); }

    function setActive(index) {
        var list = items();
        if (!list.length) { active = -1; return; }
        active = (index + list.length) % list.length;
        list.forEach(function (el, i) {
            el.classList.toggle('active', i === active);
            el.setAttribute('aria-selected', i === active ? 'true' : 'false');
        });
        list[active].scrollIntoView({block: 'nearest'});
    }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) { node.className = className; }
        if (text) { node.textContent = text; }
        return node;
    }

    function showRecent() {
        var list = readRecent();
        recentList.textContent = '';
        list.forEach(function (query) {
            var button = el('button', 'list-group-item list-group-item-action d-flex align-items-center gap-2', '');
            button.type = 'button';
            button.appendChild(el('i', 'bi bi-clock-history text-muted'));
            button.appendChild(el('span', '', query));
            button.addEventListener('click', function () { input.value = query; run(); input.focus(); });
            recentList.appendChild(button);
        });
        recentBox.classList.toggle('d-none', !list.length || input.value.trim().length >= 2);
    }

    function render(data) {
        results.textContent = '';
        active = -1;
        var total = 0;
        (data.groups || []).forEach(function (group) {
            if (!group.items || !group.items.length) { return; }
            total += group.total;
            var head = el('div', 'd-flex justify-content-between align-items-center mt-3 mb-1');
            var title = el('small', 'text-muted text-uppercase');
            title.appendChild(el('i', 'bi ' + group.icon + ' me-1'));
            title.appendChild(document.createTextNode(group.label));
            head.appendChild(title);
            if (group.total > group.items.length) {
                var more = el('a', 'small', 'ver todos (' + group.total + ')');
                more.href = group.more_url;
                head.appendChild(more);
            }
            results.appendChild(head);
            var list = el('div', 'list-group list-group-flush');
            group.items.forEach(function (item) {
                var link = el('a', 'list-group-item list-group-item-action d-flex justify-content-between align-items-center gap-2');
                link.href = item.url;
                link.setAttribute('data-search-item', '');
                link.setAttribute('role', 'option');
                var text = el('span', 'min-w-0');
                text.appendChild(el('span', 'd-block fw-semibold text-truncate', item.title));
                if (item.subtitle) { text.appendChild(el('small', 'text-muted d-block text-truncate', item.subtitle)); }
                link.appendChild(text);
                if (item.badge) { link.appendChild(el('span', 'badge bg-body-secondary text-body border flex-shrink-0', item.badge)); }
                link.addEventListener('click', function () { remember(input.value); });
                list.appendChild(link);
            });
            results.appendChild(list);
        });
        if (!total) {
            results.appendChild(el('p', 'text-muted mb-0', 'Nada encontrado para "' + data.query + '".'));
        }
        status.textContent = total ? total + ' resultado' + (total === 1 ? '' : 's') : 'Nada encontrado';
        if (total) { setActive(0); }
    }

    function run() {
        var query = input.value.trim();
        if (controller) { controller.abort(); }
        if (query.length < 2) {
            results.textContent = '';
            status.textContent = '';
            active = -1;
            showRecent();
            return;
        }
        recentBox.classList.add('d-none');
        controller = window.AbortController ? new AbortController() : null;
        fetch(suggestUrl + '?q=' + encodeURIComponent(query), {
            credentials: 'same-origin', headers: {'Accept': 'application/json'}, signal: controller ? controller.signal : undefined
        }).then(function (response) { return response.ok ? response.json() : null; })
          .then(function (data) { if (data) { render(data); } })
          .catch(function () { /* aborted or offline: keep what is on screen */ });
    }

    input.addEventListener('input', function () {
        window.clearTimeout(timer);
        timer = window.setTimeout(run, 200);
    });
    input.addEventListener('keydown', function (event) {
        if (event.key === 'ArrowDown') { event.preventDefault(); setActive(active + 1); }
        else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(active - 1); }
        else if (event.key === 'Enter') {
            var list = items();
            remember(input.value);
            if (active >= 0 && list[active]) { event.preventDefault(); window.location.href = list[active].href; }
            // otherwise the form submits to the full results page
        }
    });
    form.addEventListener('submit', function () { remember(input.value); });
    sheet.querySelector('[data-search-clear]').addEventListener('click', function () { clearRecent(); showRecent(); });

    sheet.addEventListener('shown.bs.offcanvas', function () { input.focus(); input.select(); });
    sheet.addEventListener('show.bs.offcanvas', function () { showRecent(); if (input.value.trim().length >= 2) { run(); } });

    document.querySelectorAll('[data-search-open]').forEach(function (button) {
        button.addEventListener('click', function (event) { event.preventDefault(); open(); });
    });
    document.addEventListener('keydown', function (event) {
        var typing = event.target && event.target.closest && event.target.closest('input, textarea, select, [contenteditable="true"]');
        var combo = (event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'k';
        var slash = event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !typing;
        if (combo || slash) { event.preventDefault(); open(); }
    });
})();
