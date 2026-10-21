/* Swipe rows (see css/swipe-rows.css): only one row stays open; tapping the row or outside it, or pressing Esc,
   closes it. The gesture itself is native scrolling. Delegated on the document, so rows a script adds later (the
   exercise lists of the routine forms are drawn by JavaScript) work without any setup. */
(function () {
    'use strict';
    var SELECTOR = '[data-swipe-row]';

    function isOpen(row) { return row.scrollLeft > 8; }
    // a row that is closing by itself fires scroll events too: they must not close the others
    function close(row) { if (isOpen(row)) { row.closing = true; row.scrollTo({left: 0, behavior: 'smooth'}); } }
    function closeAll(except) {
        Array.prototype.forEach.call(document.querySelectorAll(SELECTOR), function (row) { if (row !== except) close(row); });
    }

    // scroll does not bubble: listen while it travels down to its target
    document.addEventListener('scroll', function (event) {
        var row = event.target;
        if (!row || !row.matches || !row.matches(SELECTOR)) return;
        if (row.closing) {
            if (row.scrollLeft <= 1) row.closing = false;
            return;
        }
        if (isOpen(row)) closeAll(row);
    }, {capture: true, passive: true});

    document.addEventListener('click', function (event) {
        var row = event.target.closest ? event.target.closest(SELECTOR) : null;
        if (!row) { closeAll(null); return; }
        if (isOpen(row) && !event.target.closest('.swipe-actions')) {
            event.preventDefault();
            close(row);
        }
    });

    // keyboard: focusing a hidden button scrolls it into view by itself; Esc puts the row back
    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        var row = event.target.closest ? event.target.closest(SELECTOR) : null;
        if (row) close(row);
    });
})();
