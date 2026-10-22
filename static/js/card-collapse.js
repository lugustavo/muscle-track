/* Long cards fold up: a card with a title bar (.card-header) whose content is long (over CARD_MIN px) gets a chevron
   and folds / unfolds when its title bar is clicked. Short cards are left alone, and nothing changes without JavaScript.
   The choice is remembered per page and card (only "closed" is stored). A card holding a form control the browser finds
   invalid unfolds by itself, so the message is never hidden. Opt out with data-no-collapse on the card. */
(function () {
    'use strict';
    var CARD_MIN = 420;                       // px of content that makes a card "long"
    var ROOT = document.querySelector('main') || document.body;
    var seq = 0;
    var skip = '.modal, .offcanvas, .dropdown-menu, [data-no-collapse], .day-col, .form-section';   // .form-section already folds by itself

    function key(card) {
        var all = Array.prototype.slice.call(ROOT.querySelectorAll('.card'));
        return 'mt-card:' + location.pathname + ':' + (card.id || all.indexOf(card));
    }
    function remembered(card) {
        try { return localStorage.getItem(key(card)) === 'closed'; } catch (e) { return false; }
    }
    function remember(card, closed) {
        try { if (closed) localStorage.setItem(key(card), 'closed'); else localStorage.removeItem(key(card)); } catch (e) { /* private mode */ }
    }

    function enable(card) {
        var header = card.querySelector(':scope > .card-header');
        if (!header || card.cardCollapse) return;
        var body = document.createElement('div');
        body.className = 'collapse show card-collapse';
        body.id = 'card-collapse-' + (++seq);
        while (header.nextSibling) body.appendChild(header.nextSibling);
        card.appendChild(body);

        var inner = document.createElement('div');
        inner.className = 'card-toggle-main' + (header.classList.contains('d-flex') ? ' d-flex justify-content-between align-items-center gap-2' : '');
        while (header.firstChild) inner.appendChild(header.firstChild);
        var chevron = document.createElement('i');
        chevron.className = 'bi bi-chevron-down card-chevron';
        chevron.setAttribute('aria-hidden', 'true');
        header.appendChild(inner);
        header.appendChild(chevron);
        header.classList.add('card-toggle', 'd-flex', 'align-items-center');
        header.setAttribute('role', 'button');
        header.setAttribute('tabindex', '0');
        header.setAttribute('aria-controls', body.id);
        header.setAttribute('aria-expanded', 'true');
        card.cardCollapse = {header: header, body: body};

        body.addEventListener('hide.bs.collapse', function (event) {
            if (event.target !== body) return;
            header.setAttribute('aria-expanded', 'false');
            card.classList.add('is-collapsed');
        });
        body.addEventListener('show.bs.collapse', function (event) {
            if (event.target !== body) return;
            header.setAttribute('aria-expanded', 'true');
            card.classList.remove('is-collapsed');
        });
        header.addEventListener('click', function (event) {
            // a control inside the title bar does its own job (the card itself may sit inside a <form>: only look within the bar)
            var hit = event.target.closest('a, button, input, select, textarea, label, form, .btn, [data-bs-toggle]');
            if (hit && hit !== header && header.contains(hit)) return;
            toggle(card);
        });
        header.addEventListener('keydown', function (event) {
            if (event.target !== header || (event.key !== 'Enter' && event.key !== ' ')) return;
            event.preventDefault();
            toggle(card);
        });

        if (remembered(card)) {      // folded last time: start folded, without the animation
            body.classList.remove('show');
            header.setAttribute('aria-expanded', 'false');
            card.classList.add('is-collapsed');
        }
    }

    function toggle(card) {
        var c = card.cardCollapse;
        var open = c.header.getAttribute('aria-expanded') === 'true';
        var instance = bootstrap.Collapse.getOrCreateInstance(c.body, {toggle: false});
        if (open) instance.hide(); else instance.show();
        remember(card, open);
    }

    function unfold(card) {
        var c = card && card.cardCollapse;
        if (c && c.header.getAttribute('aria-expanded') === 'false') {
            bootstrap.Collapse.getOrCreateInstance(c.body, {toggle: false}).show();
            remember(card, false);
        }
    }

    function isLong(card) {
        if (card.offsetParent === null) return false;                  // hidden (another tab): looked at when it shows up
        var header = card.querySelector(':scope > .card-header');
        return card.offsetHeight - header.offsetHeight > CARD_MIN;
    }

    function candidates() {
        return Array.prototype.filter.call(ROOT.querySelectorAll('.card'), function (card) {
            return !card.cardCollapse && !card.closest(skip) && card.querySelector(':scope > .card-header');
        });
    }

    function check() {
        candidates().forEach(function (card) { if (isLong(card)) enable(card); });
    }

    if (typeof bootstrap === 'undefined') return;
    // a card that is long only later (a list filled by script, a tab opened) is picked up too
    if ('ResizeObserver' in window) {
        var observer = new ResizeObserver(function () { check(); });
        candidates().forEach(function (card) { observer.observe(card); });
    }
    document.addEventListener('shown.bs.tab', check);
    window.addEventListener('load', check);
    check();
    // an invalid field inside a folded card unfolds it (the browser could not focus it otherwise)
    document.addEventListener('invalid', function (event) { unfold(event.target.closest && event.target.closest('.card')); }, true);
})();
