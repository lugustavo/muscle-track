// Long forms on a phone: input masks, a draft that survives an accidental close, and collapsible sections that
// open by themselves when a field inside them is invalid. Driven by data attributes (see student_form.html).
(function () {
    'use strict';

    // Django's JS catalog (/jsi18n/); identity fallbacks if it has not loaded
    var gettext = window.gettext || function (s) { return s; };
    var interpolate = window.interpolate || function (s, o) {
        return s.replace(/%\((\w+)\)s/g, function (m, k) { return String(o[k]); });
    };

    // ---- Masks: data-mask="phone|cep|state" -------------------------------------------------------------
    function digitsOf(value) { return value.replace(/\D/g, ''); }

    var MASKS = {
        phone: function (value) {
            var d = digitsOf(value);
            if (d.length > 11 && d.indexOf('55') === 0) { d = d.slice(2); }   // typed with the country code
            d = d.slice(0, 11);
            if (!d.length) { return ''; }
            if (d.length <= 2) { return '(' + d; }
            if (d.length <= 6) { return '(' + d.slice(0, 2) + ') ' + d.slice(2); }
            if (d.length <= 10) { return '(' + d.slice(0, 2) + ') ' + d.slice(2, 6) + '-' + d.slice(6); }
            return '(' + d.slice(0, 2) + ') ' + d.slice(2, 7) + '-' + d.slice(7);
        },
        cep: function (value) {
            var d = digitsOf(value).slice(0, 8);
            return d.length > 5 ? d.slice(0, 5) + '-' + d.slice(5) : d;
        },
        state: function (value) { return value.replace(/[^a-zA-Z]/g, '').slice(0, 2).toUpperCase(); }
    };

    function applyMask(input) {
        var mask = MASKS[input.getAttribute('data-mask')];
        if (!mask) { return; }
        var caret = input.selectionStart;
        var atEnd = caret === null || caret >= input.value.length;
        var before = input.value.slice(0, caret || 0);
        var significant = before.replace(/[^0-9a-zA-Z]/g, '').length;   // letters/digits before the caret
        input.value = mask(input.value);
        if (atEnd) { return; }
        var pos = 0, seen = 0;
        while (pos < input.value.length && seen < significant) {
            if (/[0-9a-zA-Z]/.test(input.value.charAt(pos))) { seen++; }
            pos++;
        }
        try { input.setSelectionRange(pos, pos); } catch (e) { /* not supported on this input type */ }
    }

    document.addEventListener('input', function (event) {
        if (event.target.matches && event.target.matches('[data-mask]')) { applyMask(event.target); }
    });
    window.MTMasks = MASKS;

    var form = document.querySelector('form[data-draft]');
    if (!form) { return; }

    // ---- Sections: a field that fails the browser's own validation must not stay hidden ------------------
    function openSection(collapse) {
        if (!collapse || collapse.classList.contains('show')) { return; }
        collapse.classList.add('show');
        var toggle = document.querySelector('[data-bs-target="#' + collapse.id + '"]');
        if (toggle) { toggle.setAttribute('aria-expanded', 'true'); }
    }
    form.addEventListener('invalid', function (event) {
        openSection(event.target.closest && event.target.closest('.section-collapse'));
    }, true);

    // ---- Draft ------------------------------------------------------------------------------------------
    // Saved on this device only, for 24 hours; confidential fields (data-no-draft) are never stored, and the
    // draft is dropped when the form is submitted (the server shows the posted values again on an error).
    var KEY = 'mt-draft:' + window.location.pathname;
    var MAX_AGE_MS = 24 * 60 * 60 * 1000;
    var timer = null;

    function fields() {
        return Array.prototype.filter.call(form.elements, function (el) {
            if (!el.name || el.name === 'csrfmiddlewaretoken' || el.disabled) { return false; }
            if (['file', 'password', 'hidden', 'submit', 'button', 'reset'].indexOf(el.type) !== -1) { return false; }
            return !el.hasAttribute('data-no-draft');
        });
    }
    function snapshot() {
        var data = {};
        fields().forEach(function (el) {
            data[el.name] = (el.type === 'checkbox' || el.type === 'radio') ? (el.checked ? el.value : '') : el.value;
        });
        return data;
    }
    function sameAs(a, b) {
        return Object.keys(a).every(function (key) { return (b[key] || '') === a[key]; });
    }
    function read() {
        try {
            var raw = window.localStorage.getItem(KEY);
            if (!raw) { return null; }
            var draft = JSON.parse(raw);
            if (!draft || typeof draft.t !== 'number' || Date.now() - draft.t > MAX_AGE_MS || typeof draft.v !== 'object') {
                window.localStorage.removeItem(KEY);
                return null;
            }
            return draft;
        } catch (e) { return null; }
    }
    function drop() { try { window.localStorage.removeItem(KEY); } catch (e) { /* private mode */ } }

    var initial = snapshot();
    function save() {
        var now = snapshot();
        if (sameAs(now, initial) && sameAs(initial, now)) { drop(); return; }   // nothing typed
        try { window.localStorage.setItem(KEY, JSON.stringify({t: Date.now(), v: now})); } catch (e) { /* private mode */ }
    }
    function schedule() { window.clearTimeout(timer); timer = window.setTimeout(save, 600); }
    form.addEventListener('input', schedule);
    form.addEventListener('change', schedule);
    form.addEventListener('submit', function () { window.clearTimeout(timer); drop(); });

    function restore(draft) {
        fields().forEach(function (el) {
            if (!(el.name in draft.v)) { return; }
            if (el.type === 'checkbox' || el.type === 'radio') { el.checked = draft.v[el.name] === el.value; }
            else { el.value = draft.v[el.name]; }
            if (el.hasAttribute('data-mask')) { applyMask(el); }
            if (el.value !== initial[el.name]) { openSection(el.closest('.section-collapse')); }
        });
    }

    var draft = read();
    if (draft && !(sameAs(draft.v, initial) && sameAs(initial, draft.v))) {
        var banner = document.createElement('div');
        banner.className = 'alert alert-info d-flex flex-wrap align-items-center gap-2 justify-content-between';
        banner.setAttribute('role', 'status');
        var when = new Date(draft.t);
        var text = document.createElement('span');
        text.textContent = interpolate(gettext('Há um rascunho deste formulário salvo às %(time)s.'), {
            time: ('0' + when.getHours()).slice(-2) + ':' + ('0' + when.getMinutes()).slice(-2)
        }, true);
        var actions = document.createElement('span');
        actions.className = 'd-flex gap-2';
        var restoreButton = document.createElement('button');
        restoreButton.type = 'button'; restoreButton.className = 'btn btn-sm btn-primary'; restoreButton.textContent = gettext('Restaurar');
        var discardButton = document.createElement('button');
        discardButton.type = 'button'; discardButton.className = 'btn btn-sm btn-outline-secondary'; discardButton.textContent = gettext('Descartar');
        restoreButton.addEventListener('click', function () { restore(draft); banner.remove(); });
        discardButton.addEventListener('click', function () { drop(); banner.remove(); });
        actions.appendChild(restoreButton); actions.appendChild(discardButton);
        banner.appendChild(text); banner.appendChild(actions);
        form.insertBefore(banner, form.firstChild);
    }
})();
