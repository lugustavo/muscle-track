/* The app is not a page to pinch-zoom on a phone. Chrome / Android honours the touch-action set in css/ui.css; Safari on
   iOS does not, but reports the gesture first, so it is cancelled here. Only the photo viewer lifts the block (class
   viewer-open on <html>), so a picture can be enlarged. */
(function () {
    'use strict';
    document.addEventListener('gesturestart', function (event) {
        if (!document.documentElement.classList.contains('viewer-open')) event.preventDefault();
    }, {passive: false});
})();
