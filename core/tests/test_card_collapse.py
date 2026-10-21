"""Long cards fold up from their title bar, and every card title bar has the same look."""
import re

from django.conf import settings
from django.urls import reverse

from .base import BaseTestCase


def static_text(name):
    return (settings.BASE_DIR / 'static' / name).read_text()


class CardCollapseTests(BaseTestCase):
    def test_the_script_is_on_every_page(self):
        self.login(self.trainer)
        for name in ('home', 'routine_list', 'exercise_list'):
            page = self.client.get(reverse(f'core:{name}')).content.decode()
            self.assertIn('js/card-collapse.js', page, name)

    def test_only_long_cards_with_a_title_bar_fold_and_the_state_is_kept(self):
        script = static_text('js/card-collapse.js')
        self.assertIn('var CARD_MIN = 420', script)                       # short cards are left alone
        self.assertIn(":scope > .card-header", script)                    # a title bar is the handle
        self.assertIn('localStorage', script)                             # remembered per page and card
        self.assertIn("'invalid'", script)                                # an invalid field unfolds its card
        self.assertIn('.form-section', script)                            # the student form's own sections are left to themselves
        self.assertIn('data-no-collapse', script)                         # opt-out
        self.assertIn("aria-expanded", script)                            # keyboard / screen reader
        self.assertIn("event.key !== 'Enter'", script)

    def test_a_control_in_the_title_bar_and_a_card_inside_a_form_still_work(self):
        # the card of a form sits inside a <form>: only a control INSIDE the bar must be ignored by the click
        script = static_text('js/card-collapse.js')
        self.assertIn('header.contains(hit)', script)

    def test_the_folded_state_does_not_leave_a_blank_gap_in_a_grid(self):
        css = static_text('css/ui.css')
        self.assertIn('.card.is-collapsed { height: auto !important; }', css)
        self.assertIn('.card-toggle[aria-expanded="false"] .card-chevron { transform: rotate(-90deg); }', css)

    def test_all_title_bars_share_one_look(self):
        css = static_text('css/ui.css')
        self.assertIn('.card-header :is(h5, h6) { font-size: 1rem; font-weight: 700; margin: 0; }', css)
        self.assertIn('border-left: 4px solid var(--primary-color); font-weight: 600; }', css)


class CardConventionTests(BaseTestCase):
    """No template draws a title bar of its own kind."""
    def templates(self):
        return sorted((settings.BASE_DIR / 'templates').rglob('*.html'))

    def headers(self):
        for path in self.templates():
            text = path.read_text()
            for match in re.finditer(r'<div class="card-header[^"]*"[^>]*>(.*?)</div>', text, re.S):
                yield path.name, match.group(0)

    def test_no_light_button_in_a_title_bar(self):
        # btn-light was made for the old solid-colour bars; on the soft bars it is a white slab (and glaring in dark mode)
        bad = [name for name, bar in self.headers() if 'btn-light' in bar]
        self.assertEqual(bad, [])

    def test_no_extra_padding_variants_on_title_bars(self):
        bad = [name for name, bar in self.headers() if re.search(r'class="card-header[^"]*\bpy-3\b', bar)]
        self.assertEqual(bad, [])
