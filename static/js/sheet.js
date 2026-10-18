// Public workout sheet: "Imprimir" button; on paper the page is light even when the screen is dark and every
// "Como fazer" is open (what was closed goes back to closed afterwards).
(function () {
    var root = document.documentElement;
    var saved = null;
    var opened = [];
    var printing = false;
    window.addEventListener('beforeprint', function () {
        if (printing) return;
        printing = true;
        saved = root.getAttribute('data-bs-theme');
        root.setAttribute('data-bs-theme', 'light');
        opened = [];
        document.querySelectorAll('details:not([open])').forEach(function (d) { d.open = true; opened.push(d); });
    });
    window.addEventListener('afterprint', function () {
        if (!printing) return;
        printing = false;
        if (saved === null) { root.removeAttribute('data-bs-theme'); } else { root.setAttribute('data-bs-theme', saved); }
        opened.forEach(function (d) { d.open = false; });
        opened = [];
    });
    document.querySelectorAll('[data-print]').forEach(function (button) {
        button.addEventListener('click', function () { window.print(); });
    });
})();
