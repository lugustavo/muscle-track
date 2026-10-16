/* Swipe rows (see css/swipe-rows.css): only one row stays open; tapping the row or outside it, or pressing Esc,
   closes it. The gesture itself is native scrolling. */
(function () {
    'use strict';
    var rows = Array.prototype.slice.call(document.querySelectorAll('[data-swipe-row]'));
    if (!rows.length) return;

    function isOpen(row) { return row.scrollLeft > 8; }
    // a row that is closing by itself fires scroll events too: they must not close the others
    function close(row) { if (isOpen(row)) { row.closing = true; row.scrollTo({left: 0, behavior: 'smooth'}); } }
    function closeAll(except) { rows.forEach(function (row) { if (row !== except) close(row); }); }

    rows.forEach(function (row) {
        row.addEventListener('scroll', function () {
            if (row.closing) {
                if (row.scrollLeft <= 1) row.closing = false;
                return;
            }
            if (isOpen(row)) closeAll(row);
        }, {passive: true});
        row.addEventListener('click', function (event) {
            if (isOpen(row) && !event.target.closest('.swipe-actions')) {
                event.preventDefault();
                close(row);
            }
        });
        // keyboard: focusing a hidden button scrolls it into view by itself; Esc puts the row back
        row.addEventListener('keydown', function (event) { if (event.key === 'Escape') close(row); });
    });
    document.addEventListener('click', function (event) {
        if (!event.target.closest('[data-swipe-row]')) closeAll(null);
    });
})();
