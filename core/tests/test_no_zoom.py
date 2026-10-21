"""No pinch / double-tap zoom of the app on a phone; only the photo viewer may enlarge a picture."""
from django.conf import settings
from django.urls import reverse

from .base import BaseTestCase


def static_text(name):
    return (settings.BASE_DIR / 'static' / name).read_text()


class NoZoomTests(BaseTestCase):
    def test_the_page_blocks_pinch_and_double_tap_zoom_but_not_scrolling(self):
        css = static_text('css/ui.css')
        # manipulation first (iOS: no double-tap zoom), then pan-x pan-y (Chrome / Android: no pinch either)
        self.assertIn('html:not(.viewer-open) { touch-action: manipulation; touch-action: pan-x pan-y; }', css)

    def test_safari_on_ios_gets_its_gesture_cancelled(self):
        script = static_text('js/no-zoom.js')
        self.assertIn("'gesturestart'", script)
        self.assertIn('preventDefault', script)
        self.assertIn("viewer-open", script)

    def test_the_scripts_are_on_every_page(self):
        self.login(self.trainer)
        page = self.client.get(reverse('core:home')).content.decode()
        self.assertIn('js/no-zoom.js', page)

    def test_the_photo_viewer_lifts_the_block_while_it_is_open(self):
        script = static_text('js/photo-viewer.js')
        self.assertIn("classList.add('viewer-open')", script)
        self.assertIn("classList.remove('viewer-open')", script)
        self.assertIn('pinch-zoom', static_text('css/ui.css'))

    def test_the_viewport_still_allows_the_browsers_own_accessibility_zoom_settings(self):
        # the block is done with touch-action, not with a maximum-scale in the viewport meta (which also stops the viewer)
        self.login(self.trainer)
        page = self.client.get(reverse('core:home')).content.decode()
        self.assertNotIn('user-scalable', page)
        self.assertNotIn('maximum-scale', page)
