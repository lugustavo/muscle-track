/* In-app photo viewer: a link marked data-photo-view opens its picture in a full-screen modal instead of a new browser
   tab (the photos are private, so they stay inside the app). The links of the same gallery (the closest
   [data-photo-gallery] / photo strip / assessment card, else the whole page) can be walked with the arrows, the keyboard
   or a swipe. Without JavaScript the link still opens the picture. */
(function () {
    'use strict';
    var GALLERY = '[data-photo-gallery], [data-photo-strip], [data-my-assessment], [data-loose-photos], [data-compare]';
    var root, image, caption, counter, prev, next, modal, items = [], index = 0;

    function build() {
        if (root) return;
        root = document.createElement('div');
        root.className = 'modal fade photo-viewer';
        root.tabIndex = -1;
        root.setAttribute('aria-hidden', 'true');
        root.setAttribute('aria-label', gettext('Foto'));
        root.innerHTML =
            '<div class="modal-dialog modal-dialog-centered modal-fullscreen-sm-down modal-lg"><div class="modal-content bg-dark text-white">' +
            '<div class="modal-header border-0"><div class="photo-viewer-caption small flex-grow-1"></div>' +
            '<button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div>' +
            '<div class="modal-body p-0 position-relative text-center">' +
            '<img class="photo-viewer-img" alt="">' +
            '<button type="button" class="photo-viewer-nav prev" data-photo-prev><i class="bi bi-chevron-left" aria-hidden="true"></i></button>' +
            '<button type="button" class="photo-viewer-nav next" data-photo-next><i class="bi bi-chevron-right" aria-hidden="true"></i></button>' +
            '</div><div class="modal-footer border-0 justify-content-center small photo-viewer-count"></div></div></div>';
        document.body.appendChild(root);
        image = root.querySelector('.photo-viewer-img');
        caption = root.querySelector('.photo-viewer-caption');
        counter = root.querySelector('.photo-viewer-count');
        prev = root.querySelector('[data-photo-prev]');
        next = root.querySelector('[data-photo-next]');
        root.querySelector('.btn-close').setAttribute('aria-label', gettext('Fechar'));
        prev.setAttribute('aria-label', gettext('Foto anterior'));
        next.setAttribute('aria-label', gettext('Próxima foto'));
        prev.addEventListener('click', function () { go(-1); });
        next.addEventListener('click', function () { go(1); });
        root.addEventListener('keydown', function (event) {
            if (event.key === 'ArrowLeft') go(-1);
            if (event.key === 'ArrowRight') go(1);
        });
        var startX = null;
        root.addEventListener('touchstart', function (event) { startX = event.touches.length === 1 ? event.touches[0].clientX : null; }, {passive: true});
        root.addEventListener('touchend', function (event) {
            if (startX === null || !event.changedTouches.length) return;
            var dx = event.changedTouches[0].clientX - startX;
            startX = null;
            if (Math.abs(dx) > 60) go(dx < 0 ? 1 : -1);
        }, {passive: true});
        root.addEventListener('hidden.bs.modal', function () { image.removeAttribute('src'); });
        modal = new bootstrap.Modal(root);
    }

    function show() {
        var link = items[index];
        image.src = link.getAttribute('href');
        var thumb = link.querySelector('img');
        image.alt = link.getAttribute('data-photo-caption') || (thumb ? thumb.alt : '');
        caption.textContent = image.alt;
        counter.textContent = items.length > 1 ? (index + 1) + ' / ' + items.length : '';
        prev.style.display = next.style.display = items.length > 1 ? '' : 'none';
    }

    function go(step) {
        if (items.length < 2) return;
        index = (index + step + items.length) % items.length;
        show();
    }

    document.addEventListener('click', function (event) {
        var link = event.target.closest ? event.target.closest('a[data-photo-view]') : null;
        if (!link || event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey || typeof bootstrap === 'undefined') return;
        event.preventDefault();
        build();
        var gallery = link.closest(GALLERY) || document;
        items = Array.prototype.slice.call(gallery.querySelectorAll('a[data-photo-view]'));
        index = Math.max(items.indexOf(link), 0);
        show();
        modal.show();
    });
})();
